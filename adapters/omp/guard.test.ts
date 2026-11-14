import { describe, expect, test } from "bun:test";
import { mkdtempSync, mkdirSync, realpathSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { decide, type GuardPolicy } from "./guard";

const cwd = realpathSync(mkdtempSync(join(tmpdir(), "t12-guard-")));
mkdirSync(join(cwd, "src"));
mkdirSync(join(cwd, "docs"));
writeFileSync(join(cwd, "src", "a.ts"), "");

const blocked = (p: GuardPolicy | null, tool: string, input: Record<string, unknown>) =>
	decide(p, tool, input, cwd).block;

const hiddenPolicy: GuardPolicy = { hidden: ["tests/**", "**/*.test.ts"], bash: "allow" };

describe("no policy", () => {
	test("allows everything", () => {
		for (const [tool, input] of [
			["read", { path: "tests/a.ts" }],
			["write", { path: "/etc/passwd", content: "x" }],
			["bash", { command: "rm -rf /" }],
			["eval", { language: "py", code: "1" }],
			["grep", { pattern: "x" }],
		] as const) {
			expect(blocked(null, tool, input)).toBe(false);
			expect(blocked(undefined, tool, input)).toBe(false);
		}
	});

	test("empty policy object allows paths and bash", () => {
		expect(blocked({}, "write", { path: "src/a.ts", content: "" })).toBe(false);
		expect(blocked({}, "bash", { command: "ls" })).toBe(false);
	});
});

describe("hidden", () => {
	test("read/write/edit of hidden paths are blocked, others allowed", () => {
		expect(blocked(hiddenPolicy, "read", { path: "tests/unit/a.py" })).toBe(true);
		expect(blocked(hiddenPolicy, "read", { path: "src/a.test.ts" })).toBe(true);
		expect(blocked(hiddenPolicy, "read", { path: "tests/unit/a.py:10-20" })).toBe(true);
		expect(blocked(hiddenPolicy, "read", { path: "./tests/x" })).toBe(true);
		expect(blocked(hiddenPolicy, "read", { path: join(cwd, "tests/x") })).toBe(true);
		expect(blocked(hiddenPolicy, "write", { path: "tests/new.py", content: "" })).toBe(true);
		expect(blocked(hiddenPolicy, "write", { path: "src/b.test.ts", content: "" })).toBe(true);
		expect(blocked(hiddenPolicy, "edit", { path: "tests/a.py" })).toBe(true);
		expect(blocked(hiddenPolicy, "edit", { input: "[src/x.test.ts#1A2B]\nPUT 1.=1:\n+x" })).toBe(true);
		expect(blocked(hiddenPolicy, "read", { path: "src/a.ts" })).toBe(false);
		expect(blocked(hiddenPolicy, "write", { path: "src/a.ts", content: "" })).toBe(false);
		expect(blocked(hiddenPolicy, "edit", { input: "[src/a.ts#1A2B]\nPUT 1.=1:\n+x" })).toBe(false);
	});

	test("path lists and wrapper forms cannot smuggle hidden paths", () => {
		expect(blocked(hiddenPolicy, "read", { path: "src/a.ts; tests/x.py" })).toBe(true);
		expect(blocked(hiddenPolicy, "read", { path: "src/a.ts tests/x.py" })).toBe(true);
		expect(blocked(hiddenPolicy, "read", { path: "[tests/x.py#AB12]" })).toBe(true);
		expect(blocked(hiddenPolicy, "read", { path: "src/../tests/x.py" })).toBe(true);
	});

	test("hidden path through a symlink is blocked", () => {
		mkdirSync(join(cwd, "tests-real"));
		symlinkSync(join(cwd, "tests-real"), join(cwd, "link-to-tests"));
		const p: GuardPolicy = { hidden: ["tests-real/**"] };
		expect(blocked(p, "read", { path: "link-to-tests/x" })).toBe(true);
	});

	test("grep/find/glob/ast_grep are blocked when hidden is non-empty", () => {
		for (const tool of ["grep", "find", "glob", "ast_grep", "ast-grep"]) {
			expect(blocked(hiddenPolicy, tool, { path: "src" })).toBe(true);
			expect(blocked({ hidden: [] }, tool, { path: "src" })).toBe(false);
		}
	});

	test("lsp reads honour hidden and workspace-wide queries are blocked", () => {
		expect(blocked(hiddenPolicy, "lsp", { action: "hover", file: "tests/a.py", line: 1 })).toBe(true);
		expect(blocked(hiddenPolicy, "lsp", { action: "symbols", file: "*", query: "x" })).toBe(true);
		expect(blocked(hiddenPolicy, "lsp", { action: "hover", file: "src/a.ts", line: 1 })).toBe(false);
	});

	test("xd:// dispatch is checked as the mounted tool", () => {
		expect(
			blocked(hiddenPolicy, "write", { path: "xd://ast_edit", content: JSON.stringify({ paths: ["tests/a.py"], ops: [] }) }),
		).toBe(true);
		expect(
			blocked({ bash: "deny" }, "write", { path: "xd://bash", content: JSON.stringify({ command: "ls" }) }),
		).toBe(true);
		expect(blocked({ bash: "deny" }, "write", { path: "xd://bash", content: "not json" })).toBe(true);
	});
});

describe("read_only and writable", () => {
	const ro: GuardPolicy = { read_only: ["eslint.config.*", ".t12/**"], bash: "allow" };

	test("read_only: writes blocked, reads allowed", () => {
		expect(blocked(ro, "write", { path: "eslint.config.js", content: "" })).toBe(true);
		expect(blocked(ro, "write", { path: ".t12/state.json", content: "" })).toBe(true);
		expect(blocked(ro, "edit", { input: "[eslint.config.mjs#0000]\nPUT 1.=1:\n+x" })).toBe(true);
		expect(blocked(ro, "edit", { input: "[src/a.ts#0000]\nMV eslint.config.js" })).toBe(true);
		expect(blocked(ro, "edit", { input: "*** Begin Patch\n*** Update File: .t12/x\n*** End Patch" })).toBe(true);
		expect(blocked(ro, "edit", { path: "src/a.ts", edits: [{ op: "update", rename: ".t12/y" }] })).toBe(true);
		expect(blocked(ro, "ast_edit", { paths: ["eslint.config.js"], ops: [{ pat: "a", out: "b" }] })).toBe(true);
		expect(blocked(ro, "ast_edit", { paths: ["src"], ops: [{ pat: "a", out: "b" }] })).toBe(true);
		expect(blocked(ro, "ast_edit", { paths: ["src/*.ts"], ops: [{ pat: "a", out: "b" }] })).toBe(true);
		expect(blocked(ro, "ast_edit", { paths: ["src/a.ts"], ops: [{ pat: "a", out: "b" }] })).toBe(false);
		expect(blocked(ro, "read", { path: "eslint.config.js" })).toBe(false);
		expect(blocked(ro, "read", { path: ".t12/state.json" })).toBe(false);
		expect(blocked(ro, "grep", { pattern: "x" })).toBe(false);
		expect(blocked(ro, "write", { path: "src/a.ts", content: "" })).toBe(false);
	});

	test("writable allowlist: writes only inside it, reads anywhere", () => {
		const w: GuardPolicy = { writable: ["src/**", "docs/*.md"] };
		expect(blocked(w, "write", { path: "src/a.ts", content: "" })).toBe(false);
		expect(blocked(w, "write", { path: "src/deep/b.ts", content: "" })).toBe(false);
		expect(blocked(w, "write", { path: "docs/x.md", content: "" })).toBe(false);
		expect(blocked(w, "write", { path: "README.md", content: "" })).toBe(true);
		expect(blocked(w, "write", { path: "docs/x.txt", content: "" })).toBe(true);
		expect(blocked(w, "edit", { input: "[README.md#0000]\nPUT 1.=1:\n+x" })).toBe(true);
		expect(blocked(w, "read", { path: "README.md" })).toBe(false);
	});

	test("writable null means anywhere inside cwd", () => {
		expect(blocked({ writable: null }, "write", { path: "anything/x.txt", content: "" })).toBe(false);
	});

	test("edit with no recognizable path fails closed only when restricted", () => {
		expect(blocked({ writable: ["src/**"] }, "edit", { input: "garbage" })).toBe(true);
		expect(blocked({}, "edit", { input: "garbage" })).toBe(false);
	});

	test("hashline TAG-less tool_call views carrying path/paths are honoured", () => {
		expect(blocked(ro, "edit", { path: "eslint.config.js", paths: ["src/a.ts"] })).toBe(true);
		expect(blocked(ro, "edit", { path: "src/a.ts", paths: ["eslint.config.js"] })).toBe(true);
	});
});

describe("escaping cwd", () => {
	test("writes outside cwd are blocked even with an empty policy object", () => {
		expect(blocked({}, "write", { path: "../outside.txt", content: "" })).toBe(true);
		expect(blocked({}, "write", { path: "/tmp/outside.txt", content: "" })).toBe(true);
		expect(blocked({}, "write", { path: "~/x", content: "" })).toBe(true);
		expect(blocked({}, "edit", { input: "[../x.ts#0000]\nPUT 1.=1:\n+x" })).toBe(true);
		expect(blocked({}, "ast_edit", { paths: ["../x.ts"], ops: [] })).toBe(true);
		expect(blocked({}, "write", { path: "src/../src/ok.ts", content: "" })).toBe(false);
	});

	test("write through a symlink that leaves cwd is blocked", () => {
		const outside = realpathSync(mkdtempSync(join(tmpdir(), "t12-guard-out-")));
		symlinkSync(outside, join(cwd, "escape"));
		expect(blocked({}, "write", { path: "escape/x.txt", content: "" })).toBe(true);
	});

	test("reads outside cwd stay allowed without confine", () => {
		expect(blocked({}, "read", { path: "../x" })).toBe(false);
		expect(blocked({}, "write", { path: "local://notes.md", content: "" })).toBe(false);
		expect(blocked({}, "write", { path: "ssh://host/x", content: "" })).toBe(true);
	});
});

describe("glob semantics (match adapters/workspace.py)", () => {
	test("* crosses /", () => {
		const p: GuardPolicy = { hidden: ["src/*.ts"] };
		expect(blocked(p, "read", { path: "src/a/b.ts" })).toBe(true);
		expect(blocked(p, "read", { path: "src/a.ts" })).toBe(true);
		expect(blocked(p, "read", { path: "src/a.py" })).toBe(false);
	});

	test("**/ matches any directory prefix including none", () => {
		const p: GuardPolicy = { hidden: ["**/*.test.ts"] };
		expect(blocked(p, "read", { path: "a.test.ts" })).toBe(true);
		expect(blocked(p, "read", { path: "x/y/a.test.ts" })).toBe(true);
		expect(blocked(p, "read", { path: "a.ts" })).toBe(false);
	});

	test("dir/** matches everything under dir", () => {
		const p: GuardPolicy = { hidden: ["tests/**"] };
		expect(blocked(p, "read", { path: "tests/a" })).toBe(true);
		expect(blocked(p, "read", { path: "tests/a/b/c.py" })).toBe(true);
		expect(blocked(p, "read", { path: "src/tests/a" })).toBe(false);
	});

	test("? and character classes", () => {
		const p: GuardPolicy = { hidden: ["f?.[ab]", "g[!0-9].x"] };
		expect(blocked(p, "read", { path: "f1.a" })).toBe(true);
		expect(blocked(p, "read", { path: "f1.c" })).toBe(false);
		expect(blocked(p, "read", { path: "gz.x" })).toBe(true);
		expect(blocked(p, "read", { path: "g5.x" })).toBe(false);
	});
});

describe("bash", () => {
	test("deny", () => {
		expect(blocked({ bash: "deny" }, "bash", { command: "ls" })).toBe(true);
	});

	test("allow", () => {
		expect(blocked({ bash: "allow" }, "bash", { command: "rm -rf build" })).toBe(false);
	});

	test("exact allowlist: only the listed command lines", () => {
		const p: GuardPolicy = { bash: ["npm run lint", "pytest -q"] };
		expect(blocked(p, "bash", { command: "npm run lint" })).toBe(false);
		expect(blocked(p, "bash", { command: "  pytest -q \n" })).toBe(false);
		expect(blocked(p, "bash", { command: "npm run lint && rm -rf ." })).toBe(true);
		expect(blocked(p, "bash", { command: "npm run lint; rm -rf ." })).toBe(true);
		expect(blocked(p, "bash", { command: "npm run lint\nrm -rf ." })).toBe(true);
		expect(blocked(p, "bash", { command: "npm run" })).toBe(true);
		expect(blocked(p, "bash", { command: "npm run lint --fix" })).toBe(true);
		expect(blocked(p, "bash", {})).toBe(true);
	});

	test("empty allowlist denies", () => {
		expect(blocked({ bash: [] }, "bash", { command: "ls" })).toBe(true);
	});

	test("reason says what to do", () => {
		const d = decide({ bash: "deny" }, "bash", { command: "ls" }, cwd);
		expect(d.reason).toContain("read/edit/write");
	});
});

describe("eval and other code runners", () => {
	test("blocked unless bash is allow", () => {
		for (const tool of ["eval", "debug", "ida", "browser", "computer"]) {
			expect(blocked({ bash: "deny" }, tool, { code: "1" })).toBe(true);
			expect(blocked({ bash: ["ls"] }, tool, { code: "1" })).toBe(true);
			expect(blocked({ bash: "allow" }, tool, { code: "1" })).toBe(false);
		}
	});
});

describe("other tools", () => {
	test("host tools and unknown tools pass", () => {
		const p: GuardPolicy = { hidden: ["tests/**"], bash: "deny", writable: ["src/**"], confine: true };
		expect(blocked(p, "submit_result", { path: "tests/x", summary: "done" })).toBe(false);
		expect(blocked(p, "todo", { op: "view" })).toBe(false);
	});

	test("tts output and image inputs are path checked", () => {
		expect(blocked({ read_only: ["out/**"] }, "tts", { text: "x", output_path: "out/a.mp3" })).toBe(true);
		expect(blocked(hiddenPolicy, "generate_image", { subject: "x", input: [{ path: "tests/a.png" }] })).toBe(true);
	});

	test("lsp refactors are blocked while protected paths exist", () => {
		const p: GuardPolicy = { read_only: ["eslint.config.*"] };
		expect(blocked(p, "lsp", { action: "rename", file: "src/a.ts", line: 1, symbol: "a", new_name: "b" })).toBe(true);
		expect(blocked(p, "lsp", { action: "references", file: "src/a.ts", line: 1, symbol: "a" })).toBe(false);
		expect(blocked({}, "lsp", { action: "rename", file: "src/a.ts", line: 1, symbol: "a", new_name: "b" })).toBe(false);
	});
});

describe("confine", () => {
	const c: GuardPolicy = { confine: true, bash: "allow" };

	test("outside cwd is blocked for every file-touching tool", () => {
		expect(blocked(c, "read", { path: "../x" })).toBe(true);
		expect(blocked(c, "read", { path: "/etc/passwd" })).toBe(true);
		expect(blocked(c, "grep", { pattern: "x", path: "/home" })).toBe(true);
		expect(blocked(c, "find", { query: "x", grep_keywords: [], path: ".." })).toBe(true);
		expect(blocked(c, "glob", { path: "../**/*.ts" })).toBe(true);
		expect(blocked(c, "ast_grep", { pat: "x", path: "src; /tmp" })).toBe(true);
		expect(blocked(c, "lsp", { action: "hover", file: "../x.ts", line: 1 })).toBe(true);
		expect(blocked(c, "write", { path: "../x", content: "" })).toBe(true);
		expect(blocked(c, "edit", { input: "[../x#0000]\nPUT 1.=1:\n+x" })).toBe(true);
		expect(blocked(c, "ast_edit", { paths: ["../x.ts"], ops: [] })).toBe(true);
	});

	test(".git at root or any segment is blocked", () => {
		expect(blocked(c, "read", { path: ".git/config" })).toBe(true);
		expect(blocked(c, "read", { path: ".git" })).toBe(true);
		expect(blocked(c, "read", { path: "sub/.git/HEAD" })).toBe(true);
		expect(blocked(c, "read", { path: "src/../.git/HEAD" })).toBe(true);
		expect(blocked(c, "read", { path: ".git/objects/ab/cdef:raw" })).toBe(true);
		expect(blocked(c, "grep", { pattern: "x", path: ".git" })).toBe(true);
		expect(blocked(c, "glob", { path: ".git/**" })).toBe(true);
		expect(blocked(c, "glob", { path: "**/.git/**" })).toBe(true);
		expect(blocked(c, "glob", { path: ".g*/config" })).toBe(true);
		expect(blocked(c, "ast_grep", { pat: "x", path: ".git" })).toBe(true);
		expect(blocked(c, "lsp", { action: "hover", file: ".git/x", line: 1 })).toBe(true);
		expect(blocked(c, "write", { path: ".git/hooks/pre-commit", content: "" })).toBe(true);
		expect(blocked(c, "edit", { input: "[.git/config#0000]\nPUT 1.=1:\n+x" })).toBe(true);
		expect(blocked(c, "ast_edit", { paths: [".git"], ops: [] })).toBe(true);
	});

	test("normal in-project paths and lookalikes pass", () => {
		expect(blocked(c, "read", { path: "src/a.ts" })).toBe(false);
		expect(blocked(c, "read", { path: ".gitignore" })).toBe(false);
		expect(blocked(c, "read", { path: ".github/workflows/ci.yml" })).toBe(false);
		expect(blocked(c, "grep", { pattern: "x" })).toBe(false);
		expect(blocked(c, "grep", { pattern: "x", path: "src" })).toBe(false);
		expect(blocked(c, "glob", { path: "src/**/*.ts" })).toBe(false);
		expect(blocked(c, "write", { path: "src/new.ts", content: "" })).toBe(false);
		expect(blocked(c, "read", { path: "omp://hooks.md" })).toBe(false);
	});

	test("default is unconfined", () => {
		expect(blocked({ bash: "allow" }, "read", { path: ".git/config" })).toBe(false);
		expect(blocked({ confine: false }, "read", { path: "/etc/hosts" })).toBe(false);
	});
});

describe("confine skill reads", () => {
	const cache = realpathSync(mkdtempSync(join(tmpdir(), "t12-skills-")));
	const other = realpathSync(mkdtempSync(join(tmpdir(), "t12-other-")));
	mkdirSync(join(cache, "ponytail@abc", "ponytail"), { recursive: true });
	writeFileSync(join(cache, "ponytail@abc", "ponytail", "SKILL.md"), "");
	writeFileSync(join(other, "secret"), "");
	symlinkSync(other, join(cache, "link"));
	const c: GuardPolicy = { confine: true, bash: "allow", allow_read: [cache, `${other}/pub/**`] };
	const skill = join(cache, "ponytail@abc", "ponytail", "SKILL.md");

	test("skill:// reads pass, skill:// writes do not", () => {
		expect(blocked({ confine: true, bash: "allow" }, "read", { path: "skill://ponytail" })).toBe(false);
		expect(blocked({ confine: true, bash: "allow" }, "read", { path: "skill://ponytail/SKILL.md" })).toBe(false);
		expect(blocked({ confine: true, bash: "allow" }, "write", { path: "skill://ponytail/x", content: "" })).toBe(true);
	});

	test("absolute reads are blocked without allow_read", () => {
		expect(blocked({ confine: true, bash: "allow" }, "read", { path: skill })).toBe(true);
	});

	test("allow_read directory and glob permit reads", () => {
		expect(blocked(c, "read", { path: skill })).toBe(false);
		expect(blocked(c, "read", { path: join(cache, "ponytail@abc", "ponytail", "SKILL.md:1-20") })).toBe(false);
		expect(blocked(c, "read", { path: `${other}/pub/a.md` })).toBe(false);
		expect(blocked(c, "grep", { pattern: "x", path: join(cache, "ponytail@abc") })).toBe(false);
	});

	test("allow_read never permits writes", () => {
		expect(blocked(c, "write", { path: skill, content: "" })).toBe(true);
		expect(blocked(c, "edit", { input: `[${skill}#0000]\nPUT 1.=1:\n+x` })).toBe(true);
		expect(blocked(c, "write", { path: `${other}/pub/a.md`, content: "" })).toBe(true);
	});

	test("traversal, siblings, symlink escapes and relative entries stay blocked", () => {
		expect(blocked(c, "read", { path: join(cache, "..", "etc", "passwd") })).toBe(true);
		expect(blocked(c, "read", { path: `${cache}/ponytail@abc/../../etc/passwd` })).toBe(true);
		expect(blocked(c, "read", { path: `${cache}-sibling/x` })).toBe(true);
		expect(blocked(c, "read", { path: join(cache, "link", "secret") })).toBe(true);
		expect(blocked(c, "read", { path: `${other}/secret` })).toBe(true);
		expect(blocked({ confine: true, allow_read: ["relative/dir"] }, "read", { path: "relative/dir/x" })).toBe(false);
		expect(blocked({ confine: true, allow_read: ["relative/dir"] }, "read", { path: "/etc/passwd" })).toBe(true);
	});

	test("hidden still applies and .git inside cwd stays blocked", () => {
		expect(blocked({ ...c, hidden: ["tests/**"] }, "read", { path: "tests/a.ts" })).toBe(true);
		expect(blocked(c, "read", { path: ".git/config" })).toBe(true);
	});
});
