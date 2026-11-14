#!/usr/bin/env python3
"""Drive runs to local-verified, or to a human, with no one in the inner loop.

Plain code routes every step (Factor 8). Each pass admits queued tasks
through `t12-queue`, then takes each run's `next` from `t12 board --json`:

- `tdd-step`, `gate` and `check` run as `t12` commands with no role and
  no session. No model is called.
- Any other `<role>: ...` (analyzer, red, green, refactor, reviewer) is a
  worker step: one `omp --mode rpc` session. `t12 context` builds the
  prompt, the role's tools are mounted as host tools, and the final
  answer is a schema-checked tool call (`parsed_by` host-tool) that
  `t12 act` takes. A call `act` refuses goes back to the model in the
  same session, with the error. The factory owns the prompt, the
  built-in tool list per role kind and a guard extension, and keeps the
  whole frame stream as evidence.
- green and refactor are blind: omp runs in a worktree of the run with
  every test file (the profile's test_globs) deleted, under a guard that
  denies the shell and anything outside it. On `green_ready` or
  `refactor_done` the runner exports the worktree's change as a patch and
  hands it to `t12 act --patch`; the core applies it to the run's
  worktree, runs the red tests and lint, and answers. A patch that
  touches a hidden path is refused in the session. The blind worktree is
  removed after the step.
- A role the profile pins skills for (`context.skills`) gets exactly
  those: each is verified against its pinned hash in the skills cache
  (a missing or changed one refuses the step, naming the sync command),
  mounted through a `--config` overlay with every other skill provider
  off, and made readable under the guard. Other roles keep `--no-skills`.
- Every session's transcript starts with a meta line: the mounted skills
  and the model and thinking level passed to omp (null when omp's profile
  decides). `--role-model ROLE=MODEL` and `--role-thinking ROLE=LEVEL`
  override `--omp-model` for one role, e.g. a stronger reviewer.
- `human: ...`, `stopped: ...` and a spent budget are reported and
  skipped. The runner never answers an escalation and never merges.

The runner writes no state. The run records and event logs are the
only state, so it can be killed at any point and started again
anywhere. Within one invocation it remembers which runs a step did not
move and leaves them alone, and it stops when a pass moves no run.

Host-side, like the other adapters: it is the one piece that calls a
model, and the core never imports it.
"""

import argparse
import fcntl
import json
import os
import shlex
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass, field

import fb_report
import omp_rpc
import skills
import workspace

BIN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin")
CONTEXT_MARKER = "--- context (JSON) ---"
DETERMINISTIC = ("tdd-step", "gate", "check")


def t12(*args, timeout=None):
    return subprocess.run([os.path.join(BIN, "t12"), *args],
                          capture_output=True, text=True, timeout=timeout)


def board(home):
    result = t12("board", "--home", home, "--json")
    if result.returncode != 0:
        raise SystemExit(f"error: board failed: {result.stderr.strip()}")
    return json.loads(result.stdout)


def ops_left(home, task_id):
    with open(os.path.join(home, "runs", f"{task_id}.json"), encoding="utf-8") as fh:
        budgets = json.load(fh)["budgets"]
    return budgets["op_limit"] - budgets["ops_used"]


def position(row):
    """Where a run stands. A step that leaves this unchanged did not move
    the run, and taking it again would not either. The tdd step, cycle and
    passed-gate count matter: a refactor that leads to the next red, or a
    gate passed with another still to run, leaves the phase and `next` alone."""
    return (row["phase"], row["next"], row["candidate"],
            row.get("tdd_step"), row.get("cycle"), row.get("gates_passed"))


def waiting_reason(row):
    """Why no step can be taken for this run, or None if one can."""
    nxt = row["next"]
    if nxt.startswith(("human: ", "stopped: ")):
        return nxt
    if row["busy"]:
        return None
    for key, label in (("time_left_s", "time"), ("ops_left", "operation"),
                       ("repairs_left", "repair")):
        floor = 0 if key == "repairs_left" else 1
        if row[key] is not None and row[key] < floor:
            return f"{label} budget exhausted; stop it or prepare it again with --supersede"
    return None


def context_json(stdout):
    """The context JSON after the last marker line. The marker must end the
    line: a context string (a carried patch, a prompt quoting this code) can
    hold the marker text, but JSON escapes its newlines, so only the real
    marker line is followed by a raw one."""
    return json.loads(stdout.rsplit(CONTEXT_MARKER + "\n", 1)[1])


THINKING_LEVELS = ("off", "minimal", "low", "medium", "high", "xhigh", "max", "auto")


@dataclass
class OmpConfig:
    cmd: list
    profile: str
    model: str | None = None
    max_cost: float | None = None
    skills_cache: str | None = None
    role_models: dict = field(default_factory=dict)
    role_thinking: dict = field(default_factory=dict)

    def model_for(self, role):
        """The role's own model, else --omp-model, else None (the profile's)."""
        return self.role_models.get(role, self.model)

    def thinking_for(self, role):
        return self.role_thinking.get(role)


def parse_role_flag(entries, flag, allowed=None):
    """`ROLE=VALUE` entries as a dict; ValueError names the first bad one.
    Neither side may be empty, a role may appear once, and `allowed`
    (when given) lists the values the right side may take."""
    parsed = {}
    for entry in entries or ():
        role, sep, value = entry.partition("=")
        if not sep or not role or not value:
            raise ValueError(f"{flag} {entry!r}: expected ROLE=VALUE with both parts non-empty")
        if role in parsed:
            raise ValueError(f"{flag}: role {role!r} given more than once")
        if allowed and value not in allowed:
            raise ValueError(f"{flag} {entry!r}: {value!r} is not one of {', '.join(allowed)}")
        parsed[role] = value
    return parsed


def _roots(paths):
    """Each protected root as itself and as everything under it, the way
    `model.under` reads a protected path."""
    return [p for root in paths for p in (root, root.rstrip("/") + "/**")]


EDITOR_TOOLS = ["read", "grep", "find", "glob", "edit", "write"]
CHECKER_TOOLS = ["read", "grep", "find", "glob", "bash"]


def _protected(ctx):
    return _roots(ctx.get("protected_paths", []))


def _blind_guard(ctx):
    """The blind worktree has no test file; read_only also stops a role
    from creating one, and confine keeps it inside its cwd (and out of
    .git, the object store it shares with the run's worktree)."""
    return {"read_only": ctx.get("test_globs", []) + _protected(ctx),
            "bash": "deny", "confine": True}


# Factors 2, 3 and 8: what an omp worker may use is set here by role kind, not
# by the prompt. Nothing outside these lists (task, ask, eval, web_search) is
# ever mounted. `guard` returns the policy adapters/omp/guard.ts enforces.
# A role with a `patch_tool` is blind: it runs in a worktree without the
# test files, and that call carries the patch it made.
ROLE_POLICY = {
    "analyzer": {"tools": [], "max_turns": 4,
                 "guard": lambda ctx: {"writable": [], "bash": "deny"}},
    "red": {"tools": [*EDITOR_TOOLS, "bash"], "max_turns": 20,
            "guard": lambda ctx: {"read_only": _protected(ctx)}},
    "green": {"tools": EDITOR_TOOLS, "max_turns": 20, "guard": _blind_guard,
              "patch_tool": "green_ready"},
    "refactor": {"tools": EDITOR_TOOLS, "max_turns": 15, "guard": _blind_guard,
                 "patch_tool": "refactor_done"},
    "review": {"tools": CHECKER_TOOLS, "max_turns": 30,
               "guard": lambda ctx: {"writable": []}},
}
# What a blind role's prompt leaves out of the context: where the tests are,
# the real worktree they sit in, and the guard's own path lists.
BLIND_HIDDEN_FIELDS = ("test_globs", "protected_paths", "worktree")
OMP_PROMPT = os.path.join(os.path.dirname(BIN), "prompts", "omp-worker.md")
OMP_GUARD = os.path.join(os.path.dirname(BIN), "adapters", "omp", "guard.ts")


def role_kind(home, task_id, role, context):
    """The kind of `role`: from the run's frozen role map, else the context's
    role entry, else the role's own name."""
    entry = (_run_record(home, task_id).get("role_map") or {}).get(role) \
        or (context.get("roles") or {}).get(role) or {}
    return entry.get("kind", role)


def blind_prompt(stdout, context):
    """The prompt text with the context JSON minus BLIND_HIDDEN_FIELDS."""
    head = stdout.rsplit(CONTEXT_MARKER + "\n", 1)[0]
    shown = {k: v for k, v in context.items() if k not in BLIND_HIDDEN_FIELDS}
    return head + CONTEXT_MARKER + "\n" + json.dumps(shown, indent=2) + "\n"


def _write(path, text):
    with open(path, "w", encoding="utf-8", errors="surrogateescape", newline="") as fh:
        fh.write(text)


SKILL_PROVIDERS = ("enableAgentsUser", "enableAgentsProject", "enableClaudeUser",
                   "enableClaudeProject", "enablePiUser", "enablePiProject")


def mount_skills(pinned, cache, overlay):
    """Verify each pinned skill against the cache and write the omp config
    overlay that loads exactly those. Returns the mount dirs; raises
    skills.SkillError when one is missing or does not hash to its pin.
    Every provider that could add a skill (the user's, the product repo's)
    is off, so nothing but the pins is offered. JSON strings and lists are
    valid YAML."""
    mounts = [skills.verify(cache, pin["name"], pin["rev"], pin["sha256"]) for pin in pinned]
    lines = ["skills:",
             f"  customDirectories: {json.dumps(mounts)}",
             f"  includeSkills: {json.dumps([pin['name'] for pin in pinned])}"]
    lines += [f"  {key}: false" for key in SKILL_PROVIDERS]
    _write(overlay, "\n".join(lines) + "\n")
    return mounts


def omp_worker_step(home, row, role, omp, scratch, attempt=None):
    """A worker step as one omp RPC session (Factors 1, 4, 9): the role's
    tools are mounted as host tools and the model finishes by calling one.
    A call `act` refuses goes back to the model in the same session. For a
    blind role the session runs in a worktree without the test files, and
    its patch rides along with the call that says it is done."""
    task_id = row["task_id"]
    ctx = t12("context", "--home", home, "--task-id", task_id,
              "--role", role, "--phase", row["phase"])
    if ctx.returncode != 0:
        return f"context failed: {ctx.stderr.strip()}"
    context = context_json(ctx.stdout)
    worktree = context["worktree"]
    kind = role_kind(home, task_id, role, context)
    policy = ROLE_POLICY.get(kind)
    if policy is None:
        return f"{role} step failed: no omp tool policy for role kind {kind!r}"
    patch_tool = policy.get("patch_tool")
    test_globs = context.get("test_globs") or []
    if patch_tool and not test_globs:
        return f"{role} step failed: the context names no test_globs to hide"
    schema = t12("tool-schema", "--home", home, "--task-id", task_id, "--role", role,
                 "--host-tools")
    if schema.returncode != 0:
        return f"tool-schema failed: {schema.stderr.strip()}"
    host_tools = json.loads(schema.stdout)
    session = f"{role}-{uuid.uuid4().hex[:16]}"
    base = os.path.join(scratch, f"{task_id}-{session}")
    guard_path, transcript = base + ".guard.json", base + ".transcript.jsonl"
    pinned = context.get("skills") or []
    mounts, overlay = [], base + ".skills.yml"
    if pinned:
        cache = omp.skills_cache or os.path.join(home, "skills")
        try:
            mounts = mount_skills(pinned, cache, overlay)
        except skills.SkillError as exc:
            profile_json = os.path.join(_run_record(home, task_id)["repo"], ".t12", "project.json")
            return (f"{role} step refused: {exc}; run python3 adapters/skills.py sync "
                    f"--profile-json {profile_json} --cache {cache}")
    guard = policy["guard"](context)
    if mounts:
        guard["allow_read"] = mounts
    with open(guard_path, "w", encoding="utf-8") as fh:
        json.dump(guard, fh)
    dest = os.path.join(scratch, f"blind-{session}") if patch_tool else None
    try:
        cwd, blind_base = worktree, None
        if dest:
            try:
                blind_base = workspace.blind_worktree(worktree, dest, test_globs)
            except workspace.WorkspaceError as exc:
                return f"{role} step failed: no blind worktree: {exc}"
            cwd = dest
        env = dict(os.environ, T12_TASK_ID=task_id, T12_ROLE=role, T12_SESSION=session,
                   T12_WORKTREE=cwd, T12_GUARD_POLICY=guard_path)
        if attempt:
            attempt.start()
            env.update(attempt.env(role))
        time_left = max(1, row["time_left_s"] or 1)
        model, thinking = omp.model_for(role), omp.thinking_for(role)
        argv = omp_rpc.omp_argv(
            omp.cmd, profile=omp.profile, model=model, thinking=thinking, tools=policy["tools"],
            system_prompt=OMP_PROMPT, extensions=[OMP_GUARD],
            session_dir=os.path.join(scratch, "sessions", session),
            max_time=time_left, max_cost=omp.max_cost,
            config=[overlay] if mounts else (), skills_enabled=bool(mounts))
        with open(transcript, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"dir": "meta", "skills": pinned, "model": model,
                                 "thinking": thinking}) + "\n")
        accepted = []
        calls = 0

        def on_call(name, arguments):
            nonlocal calls
            if ops_left(home, task_id) < 1:
                return False, f"no operations left in this run's budget; {name} was not recorded", False
            extra = []
            if name == patch_tool:
                try:
                    patch = workspace.export_patch(dest, blind_base, test_globs)
                except workspace.WorkspaceError as exc:
                    return False, str(exc), False
                patch_path = f"{base}-call{calls + 1}.patch"
                _write(patch_path, patch)
                extra = ["--patch", patch_path]
            calls += 1
            call_path = f"{base}-call{calls}.json"
            with open(call_path, "w", encoding="utf-8") as fh:
                json.dump({"tool": name, "args": arguments}, fh)
            act = t12("act", "--home", home, "--task-id", task_id, "--role", role,
                      "--session", session, "--input", call_path, "--parsed-by", "host-tool",
                      "--raw", transcript, *extra)
            if act.returncode == 0:
                accepted.append(act.stdout.strip())
                return True, accepted[-1], True
            return False, act.stderr.strip(), False

        outcome = omp_rpc.run_worker(
            argv, cwd=cwd, env=env,
            prompt=blind_prompt(ctx.stdout, context) if patch_tool else ctx.stdout,
            host_tools=host_tools, on_call=on_call, max_turns=policy["max_turns"],
            timeout_s=time_left, transcript_path=transcript)
    finally:
        if dest:
            try:
                workspace.remove(dest)
            except workspace.WorkspaceError as exc:
                print(f"warning: blind worktree {dest} not removed: {exc}", file=sys.stderr)
    if outcome.status == "accepted" and accepted:
        return accepted[-1]
    return f"{role} {outcome.status}: {outcome.detail}"


def step(home, row, scratch, attempt=None, omp=None):
    """Take the one step `next` names. Returns a line for the log."""
    nxt = row["next"]
    if nxt in DETERMINISTIC:
        result = t12(nxt, "--home", home, "--task-id", row["task_id"])
        return (result.stdout or result.stderr).strip()
    role, _, _ = nxt.partition(": ")
    return omp_worker_step(home, row, role, omp, scratch, attempt)


def notify(notify_cmd, message):
    print(message)
    if notify_cmd:
        try:
            subprocess.run([*notify_cmd, message], timeout=60)
        except (OSError, subprocess.TimeoutExpired) as exc:
            print(f"warning: notify failed: {exc}", file=sys.stderr)


def drive(home, queue, max_active, notify_cmd, omp):
    """Pass after pass until no step moves a run. A run whose step left it
    where it was is not stepped again in this invocation, so a worker that
    cannot get past a refusal costs one call per invocation, not a loop."""
    stuck = {}
    attempts = {}
    version = (fb_report.config_version(omp.cmd, os.path.dirname(BIN))
               if fb_report.enabled() else "")
    try:
        with tempfile.TemporaryDirectory(prefix="t12-runner-") as scratch:
            moved = True
            while moved:
                moved = False
                if queue:
                    admitted = subprocess.run(
                        [os.path.join(BIN, "t12-queue"), "--home", home, "--queue", queue,
                         "--max", str(max_active)], capture_output=True, text=True)
                    for line in (admitted.stdout + admitted.stderr).splitlines():
                        print(f"queue: {line}")
                        moved = moved or line.startswith("started ")
                for row in board(home):
                    if row["busy"] or waiting_reason(row) or row["task_id"] in stuck:
                        continue
                    attempt = None
                    if fb_report.enabled():
                        attempt = attempts.setdefault(row["task_id"], fb_report.Attempt(
                            _run_record(home, row["task_id"]), version))
                    line = step(home, row, scratch, attempt, omp)
                    print(f"{row['task_id']}: {row['next']} -> {line}")
                    after = {r["task_id"]: r for r in board(home)}[row["task_id"]]
                    if position(after) == position(row):
                        stuck[row["task_id"]] = f"{row['next']} did not move the run: {line}"
                    else:
                        moved = True
        for row in board(home):
            reason = waiting_reason(row) or stuck.get(row["task_id"])
            if reason:
                notify(notify_cmd, f"waiting {row['task_id']}: {reason}")
    finally:
        for task_id, attempt in attempts.items():
            reason = stuck.get(task_id)
            events = os.path.join(home, "runs", f"{task_id}.events.jsonl")
            attempt.finish(events, "failed" if reason else "completed", reason and reason[:200])


def _run_record(home, task_id):
    with open(os.path.join(home, "runs", f"{task_id}.json"), encoding="utf-8") as fh:
        return json.load(fh)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="runner", description=__doc__.splitlines()[0])
    parser.add_argument("--home", required=True)
    parser.add_argument("--omp-cmd", default="omp", help="the omp command")
    parser.add_argument("--omp-profile", default="t12-worker",
                        help="omp profile the workers run under")
    parser.add_argument("--omp-model", help="omp --model for the workers")
    parser.add_argument("--role-model", action="append", metavar="ROLE=MODEL",
                        help="omp --model for one role, e.g. reviewer=anthropic/claude-opus-4-1; "
                             "repeatable; other roles use --omp-model")
    parser.add_argument("--role-thinking", action="append", metavar="ROLE=LEVEL",
                        help="omp --thinking for one role (" + ", ".join(THINKING_LEVELS)
                             + "); repeatable")
    parser.add_argument("--omp-max-cost", type=float,
                        help="omp --max-cost in USD for each worker session")
    parser.add_argument("--skills-cache",
                        help="where adapters/skills.py sync put the pinned skills "
                             "(default: <home>/skills)")
    parser.add_argument("--queue", help="directory of task files to admit each pass")
    parser.add_argument("--max", type=int, default=1, help="most runs active at once")
    parser.add_argument("--notify-cmd",
                        help="command run with each waiting message as its last argument")
    args = parser.parse_args(argv)
    try:
        role_models = parse_role_flag(args.role_model, "--role-model")
        role_thinking = parse_role_flag(args.role_thinking, "--role-thinking", THINKING_LEVELS)
    except ValueError as exc:
        parser.error(str(exc))
    home = os.path.abspath(args.home)
    with open(os.path.join(home, "runner.lock"), "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("error: another runner is driving this state home", file=sys.stderr)
            return 1
        omp = OmpConfig(shlex.split(args.omp_cmd), args.omp_profile, args.omp_model,
                        args.omp_max_cost,
                        os.path.abspath(args.skills_cache or os.path.join(home, "skills")),
                        role_models, role_thinking)
        print(f"worker: omp profile {omp.profile}; the final answer is a host tool call")
        drive(home, args.queue and os.path.abspath(args.queue), args.max,
              shlex.split(args.notify_cmd) if args.notify_cmd else None, omp)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
