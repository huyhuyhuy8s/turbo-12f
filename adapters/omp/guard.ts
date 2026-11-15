/**
 * omp extension: tool-call guard for turbo-12f workers.
 *
 * Load with `omp -e adapters/omp/guard.ts`. The policy is a JSON file named by $T12_GUARD_POLICY
 * (unset: allow everything). Blocking happens before the tool runs via the `tool_call` hook.
 *
 *   {"hidden": ["tests/**"],            no read/write/edit/search may touch these paths
 *    "read_only": ["eslint.config.*"],  writes/edits blocked, reads allowed
 *    "writable": null | ["src/**"],     null: anywhere not blocked above; list: writes ONLY there
 *    "bash": "deny" | "allow" | ["exact command line", ...],
 *    "confine": true,                   nothing outside cwd or under .git (skill:// reads stay allowed)
 *    "allow_read": ["/abs/dir", "/abs/cache/**"]   absolute dirs or globs readable even under confine}
 *
 * Globs follow adapters/workspace.py: fnmatch on repo-relative POSIX paths (`*` also crosses `/`),
 * `dir/**` matches `dir` and everything under it, `**` + `/x` matches x at any depth including root.
 * Paths resolve against the session cwd; a write whose lexical or symlink-resolved target leaves
 * cwd is blocked. Search tools cannot be filtered, so they are blocked whenever `hidden` is non-empty.
 * Tools that can run arbitrary code (eval, debug, ida, browser, computer) are blocked unless
 * bash is "allow", because they can spawn shells and sidestep every path rule. `allow_read` only
 * widens reads under `confine`; writes there stay blocked.
 */

import { readFileSync, realpathSync, statSync } from "node:fs";
import { basename, dirname, isAbsolute, join, relative, resolve, sep } from "node:path";

export interface GuardPolicy {
	hidden?: string[];
	read_only?: string[];
	writable?: string[] | null;
	bash?: "deny" | "allow" | string[];
	confine?: boolean;
	allow_read?: string[];
}

export interface Decision {
	block: boolean;
	reason?: string;
}

interface Policy {
	hidden: string[];
	readOnly: string[];
	writable: string[] | null;
	bash: "deny" | "allow" | string[];
	confine: boolean;
	allowRead: string[];
}

const ALLOW: Decision = { block: false };
const SEARCH_TOOLS = new Set(["grep", "find", "glob", "ast_grep"]);
const CODE_TOOLS = new Set(["eval", "debug", "ida", "browser", "computer"]);
const LOCAL_SCHEMES = new Set(["local", "proc", "xd"]);
const GLOB_CHARS = /[*?[{]/;

const block = (reason: string): Decision => ({ block: true, reason });

function strings(v: unknown): string[] {
	return Array.isArray(v) ? v.filter((x): x is string => typeof x === "string" && x !== "") : [];
}

function normalize(policy: GuardPolicy): Policy {
	const bash = policy.bash;
	return {
		hidden: strings(policy.hidden),
		readOnly: strings(policy.read_only),
		writable: Array.isArray(policy.writable) ? strings(policy.writable) : null,
		bash: bash === undefined || bash === "allow" ? "allow" : Array.isArray(bash) ? strings(bash) : "deny",
		confine: policy.confine === true,
		allowRead: strings(policy.allow_read).filter(p => isAbsolute(p)),
	};
}


const regexCache = new Map<string, RegExp>();

/** Line-for-line port of `_glob_regex` in adapters/workspace.py. */
function globToRegExp(glob: string): RegExp {
	const cached = regexCache.get(glob);
	if (cached) return cached;
	let re = "";
	for (let i = 0; i < glob.length; ) {
		const end = glob[i] === "[" ? glob.indexOf("]", i + 2) : -1;
		if (glob.startsWith("**/", i)) {
			re += "(?:.*/)?";
			i += 3;
		} else if (glob.startsWith("**", i)) {
			re += ".*";
			i += 2;
		} else if (glob[i] === "*") {
			re += ".*";
			i++;
		} else if (glob[i] === "?") {
			re += ".";
			i++;
		} else if (end !== -1) {
			let body = glob.slice(i + 1, end);
			if (body[0] === "!") body = `^${body.slice(1)}`;
			re += `[${body.replace(/\\/g, "\\\\")}]`;
			i = end + 1;
		} else {
			re += glob[i].replace(/[.*+?^${}()|[\]\\/]/g, "\\$&");
			i++;
		}
	}
	const compiled = new RegExp(`^(?:${re})$`, "s");
	regexCache.set(glob, compiled);
	return compiled;
}

function matchesAny(globs: string[], rels: string[]): boolean {
	return globs.some(g => {
		const re = globToRegExp(g);
		return rels.some(r => re.test(r));
	});
}


function realish(p: string): string {
	const rest: string[] = [];
	let cur = p;
	for (;;) {
		try {
			const r = realpathSync(cur);
			return rest.length ? join(r, ...rest.reverse()) : r;
		} catch {
			const parent = dirname(cur);
			if (parent === cur) return p;
			rest.push(basename(cur));
			cur = parent;
		}
	}
}

function inside(root: string, target: string): string | null {
	const rel = relative(root, target);
	if (rel === ".." || rel.startsWith(`..${sep}`) || isAbsolute(rel)) return null;
	return rel.split(sep).join("/");
}

interface Target {
	rels: string[];
	escapes: boolean;
}

function locate(raw: string, cwd: string): Target {
	const home = process.env.HOME ?? "";
	const expanded = raw === "~" ? home : raw.startsWith("~/") ? join(home, raw.slice(2)) : raw;
	const abs = resolve(cwd, expanded);
	const lexical = inside(resolve(cwd), abs);
	const real = inside(realish(resolve(cwd)), realish(abs));
	const rels = [...new Set([lexical, real].filter((r): r is string => r !== null))];
	return { rels, escapes: lexical === null || real === null };
}

function clean(raw: string): string {
	const m = /^\[(.+)#[0-9A-Fa-f]{4}\]$/.exec(raw.trim());
	const s = m ? m[1] : raw.trim();
	return s.startsWith("file://") ? s.slice("file://".length) : s;
}

function scheme(p: string): string | null {
	const m = /^([a-z][a-z0-9+.-]*):\/\//i.exec(p);
	return m ? m[1].toLowerCase() : null;
}

function pieces(raw: string): string[] {
	return raw.split(/[;\n]/).map(clean).filter(p => p !== "");
}

/**
 * Every string that might name a hidden file inside one path argument: the piece itself, each
 * `:`-separated segment (selectors, archive members, sqlite tables), and whitespace/comma tokens
 * (delimited-list recovery). Over-matching only ever blocks more.
 */
function hiddenCandidates(piece: string): string[] {
	const out = new Set([piece]);
	for (const seg of piece.split(":")) out.add(seg);
	for (const tok of piece.split(/[\s,]+/)) if (tok) out.add(tok);
	return [...out].filter(c => c !== "");
}

function checkHidden(policy: Policy, piece: string, cwd: string, verb: string): Decision {
	if (policy.hidden.length === 0) return ALLOW;
	for (const cand of hiddenCandidates(piece)) {
		if (scheme(cand)) continue;
		if (matchesAny(policy.hidden, locate(cand, cwd).rels)) {
			return block(`${piece} is hidden from this task; ${verb} a different path or finish with what you can see`);
		}
	}
	return ALLOW;
}

function checkRead(policy: Policy, raw: string, cwd: string): Decision {
	for (const piece of pieces(raw)) {
		const d = checkHidden(policy, piece, cwd, "read");
		if (d.block) return d;
		const c = checkConfine(policy, piece, cwd, true);
		if (c.block) return c;
	}
	return ALLOW;
}

function readAllowed(policy: Policy, file: string, cwd: string): boolean {
	if (policy.allowRead.length === 0 || !isAbsolute(file)) return false;
	const abs = resolve(cwd, file);
	const real = realish(abs);
	return policy.allowRead.some(entry => {
		if (GLOB_CHARS.test(entry)) return matchesAny([entry], [abs]) && matchesAny([entry], [real]);
		return inside(resolve(entry), abs) !== null && inside(realish(resolve(entry)), real) !== null;
	});
}

/**
 * `confine`: the blind worktree shares the repo's object store, so anything outside cwd or under a
 * `.git` segment (literal, or a glob segment that could match one) is off limits. `skill://` and
 * `allow_read` locations are readable (`reading` only); writes never get that exemption.
 */
function checkConfine(policy: Policy, piece: string, cwd: string, reading = false): Decision {
	if (!policy.confine || scheme(piece)) return ALLOW;
	const why = `${piece} is outside the project or inside .git; use paths inside the working directory`;
	const first = piece.split(":")[0];
	if (locate(first, cwd).escapes && !(reading && readAllowed(policy, first, cwd))) return block(why);
	for (const cand of hiddenCandidates(piece)) {
		if (scheme(cand)) continue;
		if (locate(cand, cwd).rels.some(r => r.split("/").includes(".git"))) return block(why);
		if (cand.split("/").some(seg => GLOB_CHARS.test(seg) && !/^\*+$/.test(seg) && globToRegExp(seg).test(".git"))) {
			return block(why);
		}
	}
	return ALLOW;
}

function mayExpand(piece: string, cwd: string): boolean {
	if (GLOB_CHARS.test(piece)) return true;
	try {
		return statSync(resolve(cwd, piece)).isDirectory();
	} catch {
		return false;
	}
}

function checkWrite(policy: Policy, raw: string, cwd: string): Decision {
	for (const piece of pieces(raw)) {
		const confined = checkConfine(policy, piece, cwd);
		if (confined.block) return confined;
		const sch = scheme(piece);
		if (sch) {
			if (LOCAL_SCHEMES.has(sch)) continue;
			return block(`writes to ${sch}:// targets are not allowed; write a file inside the working directory`);
		}
		const hidden = checkHidden(policy, piece, cwd, "write");
		if (hidden.block) return hidden;
		const file = piece.split(":")[0];
		const t = locate(file, cwd);
		if (t.escapes) return block(`${file} is outside the working directory; write only inside it`);
		if (matchesAny(policy.readOnly, t.rels)) {
			return block(`${file} is read-only for this task; change a different file`);
		}
		if (policy.writable !== null && !matchesAny(policy.writable, t.rels)) {
			return block(`${file} is outside the writable set (${policy.writable.join(", ")}); change a file inside it`);
		}
	}
	return ALLOW;
}

/** Writes to a set of paths that may be globs or directories: provable only when nothing is restricted. */
function checkWriteExpanding(policy: Policy, raw: string, cwd: string): Decision {
	const restricted = policy.hidden.length > 0 || policy.readOnly.length > 0 || policy.writable !== null;
	for (const piece of pieces(raw)) {
		const confined = checkConfine(policy, piece, cwd);
		if (confined.block) return confined;
		if (!scheme(piece) && mayExpand(piece, cwd)) {
			if (restricted) {
				return block(`${piece} may expand to protected files; name each file to change explicitly`);
			}
			const prefix = piece.split(/[*?[{]/)[0];
			const d = checkWrite(policy, prefix === "" ? "." : prefix, cwd);
			if (d.block) return d;
			continue;
		}
		const d = checkWrite(policy, piece, cwd);
		if (d.block) return d;
	}
	return ALLOW;
}


function bashDecision(policy: Policy, command: unknown): Decision {
	if (policy.bash === "allow") return ALLOW;
	if (policy.bash === "deny") return block("shell commands are disabled for this task; use read/edit/write tools");
	if (typeof command === "string" && policy.bash.some(c => c.trim() === command.trim())) return ALLOW;
	return block(`this exact command is not allowed; permitted commands: ${policy.bash.map(c => JSON.stringify(c)).join(", ") || "none"}`);
}

function editTargets(input: Record<string, unknown>): string[] {
	const out: string[] = [];
	if (typeof input.path === "string") out.push(input.path);
	out.push(...strings(input.paths));
	if (Array.isArray(input.edits)) {
		for (const e of input.edits) {
			const rename = field(e, "rename");
			if (typeof rename === "string") out.push(rename);
		}
	}
	if (typeof input.input === "string") {
		const text = input.input;
		for (const m of text.matchAll(/^\[([^\]\n]+?)#[0-9A-Fa-f]{4}\][ \t]*$/gm)) out.push(m[1]);
		for (const m of text.matchAll(/^MV[ \t]+(.+?)[ \t]*$/gm)) out.push(m[1].replace(/^(["'])(.*)\1$/, "$2"));
		for (const m of text.matchAll(/^\*\*\* (?:Add File|Update File|Delete File|Move to|Edit File):[ \t]*(.+?)[ \t]*$/gm)) {
			out.push(m[1]);
		}
	}
	return out;
}

function decideLsp(policy: Policy, input: Record<string, unknown>, cwd: string): Decision {
	const action = typeof input.action === "string" ? input.action : "";
	const file = typeof input.file === "string" ? input.file : "";
	const mutating = ["rename", "rename_file", "code_actions", "request"].includes(action);
	const wholeWorkspace = file === "" || file === "*" || GLOB_CHARS.test(file);
	if (policy.hidden.length > 0 && wholeWorkspace && action !== "status" && action !== "capabilities" && action !== "reload") {
		return block("workspace-wide lsp queries are disabled while paths are hidden; pass one explicit file");
	}
	if (file !== "" && !wholeWorkspace) {
		const d = checkRead(policy, file, cwd);
		if (d.block) return d;
	}
	if (!mutating) return ALLOW;
	if (policy.hidden.length > 0 || policy.readOnly.length > 0 || policy.writable !== null) {
		return block(`lsp ${action} can modify files beyond ${file || "its target"}; use edit on explicit files`);
	}
	if (action === "rename_file" && typeof input.new_name === "string") return checkWrite(policy, input.new_name, cwd);
	return file !== "" && !wholeWorkspace ? checkWrite(policy, file, cwd) : ALLOW;
}

function decideDevice(policy: Policy, path: string, content: unknown, cwd: string): Decision {
	const device = /^xd:\/\/([^/?#]+)/i.exec(path)?.[1].toLowerCase();
	if (!device) return ALLOW;
	let args: unknown;
	try {
		args = typeof content === "string" ? JSON.parse(content) : content;
	} catch {
		return block(`${path} dispatch needs a JSON object in content`);
	}
	if (!args || typeof args !== "object" || Array.isArray(args)) {
		return block(`${path} dispatch needs a JSON object in content`);
	}
	return decideNormalized(policy, device, Object.fromEntries(Object.entries(args)), cwd);
}

export function decide(
	policy: GuardPolicy | null | undefined,
	toolName: string,
	input: Record<string, unknown> | null | undefined,
	cwd: string,
): Decision {
	if (!policy) return ALLOW;
	return decideNormalized(normalize(policy), toolName.replace(/-/g, "_"), input ?? {}, cwd);
}

function field(obj: unknown, key: string): unknown {
	return obj && typeof obj === "object" && key in obj ? Object.entries(obj).find(([k]) => k === key)?.[1] : undefined;
}

function decideNormalized(policy: Policy, tool: string, input: Record<string, unknown>, cwd: string): Decision {
	const str = (k: string): string | undefined => {
		const v = input[k];
		return typeof v === "string" ? v : undefined;
	};

	if (tool === "bash") return bashDecision(policy, input.command);
	if (CODE_TOOLS.has(tool)) {
		if (policy.bash === "allow") return ALLOW;
		return block(`${tool} can run arbitrary code and is disabled while bash is restricted; use read/edit/write tools`);
	}
	if (SEARCH_TOOLS.has(tool)) {
		if (policy.hidden.length === 0) {
			const p = str("path");
			return p === undefined ? ALLOW : checkRead(policy, p, cwd);
		}
		return block(`${tool} is disabled because some paths are hidden; use read on specific files you know exist`);
	}

	switch (tool) {
		case "read": {
			const p = str("path");
			return p === undefined ? ALLOW : checkRead(policy, p, cwd);
		}
		case "write": {
			const p = str("path");
			if (p === undefined) return ALLOW;
			if (/^xd:\/\//i.test(p.trim())) return decideDevice(policy, p.trim(), input.content, cwd);
			return checkWrite(policy, p, cwd);
		}
		case "edit": {
			const targets = editTargets(input);
			if (targets.length === 0) {
				const restricted = policy.hidden.length > 0 || policy.readOnly.length > 0 || policy.writable !== null;
				return restricted ? block("edit names no recognizable file path; start each section with [path#TAG]") : ALLOW;
			}
			for (const t of targets) {
				const d = checkWrite(policy, t, cwd);
				if (d.block) return d;
			}
			return ALLOW;
		}
		case "ast_edit": {
			const single = str("path");
			const paths = [...strings(input.paths), ...(single ? [single] : [])];
			if (paths.length === 0) {
				const restricted = policy.hidden.length > 0 || policy.readOnly.length > 0 || policy.writable !== null;
				return restricted ? block("ast_edit needs explicit paths") : ALLOW;
			}
			for (const p of paths) {
				const d = checkWriteExpanding(policy, p, cwd);
				if (d.block) return d;
			}
			return ALLOW;
		}
		case "lsp":
			return decideLsp(policy, input, cwd);
		case "tts": {
			const p = str("output_path");
			return p === undefined ? ALLOW : checkWrite(policy, p, cwd);
		}
		case "generate_image": {
			if (Array.isArray(input.input)) {
				for (const item of input.input) {
					const p = field(item, "path");
					if (typeof p === "string") {
						const d = checkRead(policy, p, cwd);
						if (d.block) return d;
					}
				}
			}
			return ALLOW;
		}
		default:
			return ALLOW;
	}
}


interface GuardEvent {
	toolName: string;
	input: Record<string, unknown>;
}

interface GuardApi {
	on(
		event: "tool_call",
		handler: (event: GuardEvent, ctx?: { cwd?: string }) => Promise<{ block: true; reason: string } | undefined>,
	): void;
}

function loadPolicy(file: string): { policy: GuardPolicy } | { error: string } {
	try {
		const parsed: unknown = JSON.parse(readFileSync(file, "utf8"));
		if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return { error: "policy must be a JSON object" };
		return { policy: parsed as GuardPolicy };
	} catch (e) {
		return { error: e instanceof Error ? e.message : String(e) };
	}
}

export default function guard(pi: GuardApi): void {
	const file = process.env.T12_GUARD_POLICY;
	if (!file) return;
	const loaded = loadPolicy(file);
	pi.on("tool_call", async (event, ctx) => {
		if ("error" in loaded) {
			return { block: true, reason: `guard policy unreadable (${loaded.error}); all tools refused` };
		}
		const d = decide(loaded.policy, event.toolName, event.input, ctx?.cwd ?? process.cwd());
		return d.block ? { block: true, reason: d.reason ?? "blocked by guard policy" } : undefined;
	});
}
