"""The deterministic TDD engine: what the core does with each tool call of
the analyzer, red, green and refactor roles, and the final step.

No role grades its own work. A role proposes (a test list, a red test, a
patch); this module checks the proposal against git and the project's test
and lint commands, then moves the loop to its next step or refuses with an
error the same role can act on. Green and refactor work blind: their errors
are sanitized here so no test path, test name or test list reaches them.

The module imports nothing from the CLI. The CLI hands each handler a
`Runner`: `run_on_commit(run, commit, argv, name) -> (exit_code, output,
evidence_ref)` runs a command on a commit in the verification sandbox and
stores the observation as evidence `name`; `save(event_type, detail)`
persists the run and appends an event. Every handler saves before it
raises, so a refusal is on record. Handlers mutate `run` in place and
return the message the CLI prints; a refusal raises StateError.
"""

import json
import os
import re
import time
from typing import Callable, NamedTuple

from . import gates as gate_mod
from . import gitio, globs, model
from . import profile as profile_mod
from .errors import GitError, StateError

OUTPUT_CHARS = 4000
FAILURE_LINES = 40
FALLBACK_LINES = 10
FILE_LIST_LIMIT = 200


class Runner(NamedTuple):
    run_on_commit: Callable
    save: Callable


# ------------------------------------------------------------ text helpers

def _one_line(text):
    return " ".join(text.split())


def _short_test_name(name):
    return re.split(r"::|\.", name)[-1]


def _names_test(name, line):
    short = _short_test_name(name)
    return bool(short) and re.search(rf"(?<!\w){re.escape(short)}(?!\w)", line) is not None


def _names_test_path(line, test_globs):
    """True when the line names a path a test glob matches: a file path
    (any directory prefix of it), or a dotted module path such as
    `tests.test_a.TestX.test_y`."""
    for token in re.findall(r"[\w.\-/\\]+", line):
        parts = token.replace("\\", "/").split("/")
        if any(globs.match("/".join(parts[i:]), test_globs) for i in range(len(parts))):
            return True
        dotted = token.split(".")
        for start in range(len(dotted)):
            for end in range(start + 1, len(dotted) + 1):
                if globs.match("/".join(dotted[start:end]) + ".py", test_globs):
                    return True
    return False


def _hides_tests(line, test_globs, test_names):
    return _names_test_path(line, test_globs) or any(_names_test(n, line) for n in test_names)


def sanitize_failure(output, test_globs, test_names, assertion_pattern):
    """The lines of a failing test run a blind role may see: every line
    matching `assertion_pattern` and the line after it, minus any line that
    names a test path (matching `test_globs`) or a test name."""
    lines = output.splitlines()
    pattern = re.compile(assertion_pattern)
    keep = set()
    for index, line in enumerate(lines):
        if pattern.search(line):
            keep.update((index, index + 1))
    return [lines[i] for i in sorted(keep)
            if i < len(lines) and lines[i].strip()
            and not _hides_tests(lines[i], test_globs, test_names)]


def _test_names(tdd):
    names = [i["test_name"] for i in tdd["items"] if i.get("test_name")]
    if tdd.get("red") and tdd["red"].get("test_name"):
        names.append(tdd["red"]["test_name"])
    return names


def _failure_lines(output, tdd):
    """sanitize_failure, or, when the failure is no assertion (an import
    or syntax error in production code), the last lines of the output with
    the same lines removed."""
    names = _test_names(tdd)
    lines = sanitize_failure(output, tdd["test_globs"], names, tdd["assertion_pattern"])
    if not lines:
        tail = [ln for ln in output.splitlines() if ln.strip()]
        lines = [ln for ln in tail[-FALLBACK_LINES:]
                 if not _hides_tests(ln, tdd["test_globs"], names)]
    return lines[:FAILURE_LINES]


def _production_lines(output, test_globs):
    """Lint output about files that are not tests."""
    lines = [ln for ln in output.splitlines()
             if ln.strip() and not _names_test_path(ln, test_globs)]
    return lines[-FAILURE_LINES:]


def _tail(output):
    return output.splitlines()[-FAILURE_LINES:]


def _last_output(text):
    return text[-OUTPUT_CHARS:]


# ------------------------------------------------------------ state helpers

def _tdd(run, *steps):
    tdd = run.get("tdd")
    if not tdd:
        raise StateError("this run has no tdd state; it was not prepared for the tdd lane")
    phase_ok = run["phase"] == "tdd" or (run["phase"] == "ready" and "analyzer" in steps)
    if not phase_ok or tdd["step"] not in steps:
        raise StateError(
            f"this call needs tdd step {' or '.join(steps)}; "
            f"the run is in phase {run['phase']!r}, step {tdd['step']!r}")
    return tdd


def _worktree(run):
    wt = run["worktree"]
    if not gitio.is_git_repo(wt):
        raise StateError(f"worktree missing or not a git repo: {wt}")
    return wt


def _at_last_good(run, tdd, require_clean):
    wt = _worktree(run)
    if gitio.head(wt) != tdd["last_good_commit"]:
        raise StateError(
            f"worktree HEAD {gitio.head(wt)[:12]} is not the last good commit "
            f"{tdd['last_good_commit'][:12]}")
    if require_clean and not gitio.is_clean(wt):
        raise StateError("worktree has uncommitted changes")
    return wt


def _accepted(tdd, step):
    tdd["attempts"][step] = 0
    tdd["last_error"] = None


def _pause(run, runner, question, kind):
    run["prior_phase"] = run["phase"]
    run.setdefault("escalations", []).append({
        "question": question, "kind": kind, "asked_at": time.time(),
        "answer": None, "answered_at": None,
    })
    model.transition(run, "paused")
    runner.save("human-contact", {"question": question, "kind": kind})


def _refuse(run, runner, step, event, check, message, *, exit_code=None, tail=None,
            output="", evidence=None, item=None, next_step=None):
    """Record a refused step and count the attempt. Raises StateError with
    `message`; past the attempt limit the run pauses for a human instead,
    and the pause message is returned."""
    tdd = run["tdd"]
    if next_step:
        tdd["step"] = next_step
    attempts = model.bump_attempt(tdd, step)
    tdd["last_error"] = {"check": check, "message": message, "exit_code": exit_code,
                         "tail": tail if tail is not None else _tail(output)}
    runner.save(event, {"step": step, "check": check, "message": message,
                        "exit_code": exit_code, "output": _last_output("\n".join(tdd["last_error"]["tail"])),
                        "evidence": evidence, "item": item, "attempt": attempts})
    limit = run["budgets"]["attempt_limit"]
    if attempts > limit:
        question = (f"{step} was refused {attempts} times (last check: {check}); "
                    f"the attempt limit is {limit}. A human must look.")
        _pause(run, runner, question, "attempt-limit")
        return f"paused: {question}"
    raise StateError(f"refusing {step}: {message}")


def _spec(raw):
    """A test-list item as the model's `new_item` wants it."""
    return {"text": raw["text"], "kind": raw["kind"], "source": raw["source"],
            "needed": raw.get("needed", ""), "criteria": list(raw.get("criteria") or [])}


def _bad_specs(specs, criteria_count):
    for position, spec in enumerate(specs, 1):
        if not isinstance(spec.get("text"), str) or not spec["text"].strip():
            return f"item {position} has empty text"
        for index in spec.get("criteria") or []:
            if (not isinstance(index, int) or isinstance(index, bool)
                    or not 0 <= index < criteria_count):
                return f"item {position} names unknown criterion {index!r}; criteria are 0..{criteria_count - 1}"
    return None


def _evidence_name(tdd, label):
    tdd["seq"] = tdd.get("seq", 0) + 1
    return f"tdd-{tdd['seq']:03d}-{label}.txt"


def _timed_out(ref):
    return bool(((ref or {}).get("observation") or {}).get("timed_out"))


def _passed(code, ref):
    return code == 0 and not _timed_out(ref)


def _run_checks(run, tdd, runner, commit, label):
    """Tests, then lint, on `commit` in the sandbox. The first failure
    ends it. Returns (failed, result): failed is (check, code, output, ref)
    or None; result holds each run's evidence ref."""
    result = {"tests": None, "lint": None}
    failed = None
    try:
        for check, argv in (("tests", tdd["test_command"]), ("lint", tdd.get("lint_command"))):
            if not argv:
                continue
            code, output, ref = runner.run_on_commit(
                run, commit, argv, _evidence_name(tdd, f"{label}-{check}"))
            result[check] = ref
            if not _passed(code, ref):
                failed = (check, code, output, ref)
                break
    finally:
        model.bump_op(run)
    return failed, result


# ------------------------------------------------------------------ analyze

def _finding_ids(tdd):
    """Ids of the review findings the analyzer must answer, from the
    stored report (a review tool call) or an explicit list."""
    findings = tdd.get("findings") or {}
    if findings.get("ids"):
        return list(findings["ids"])
    report = findings.get("report")
    path = report.get("path") if isinstance(report, dict) else None
    if not path:
        return []
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return []
    ids = []

    def walk(node):
        if isinstance(node, dict):
            if isinstance(node.get("findings"), list):
                ids.extend(str(f["id"]) for f in node["findings"]
                           if isinstance(f, dict) and "id" in f)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(data)
    return ids


def analyze(home, run, role, session, args, *, runner):
    tdd = _tdd(run, "analyzer")

    def refuse(check, message):
        return _refuse(run, runner, "analyzer", "tdd-list-failed", check, message)

    items = args["items"]
    directives = [d for d in args.get("directives") or [] if d.strip()]
    criteria_count = len(run.get("acceptance_criteria") or [])
    bad = _bad_specs(items, criteria_count)
    if bad:
        return refuse("test-list", bad)
    mode = tdd["mode"]
    if mode == "initial":
        if not items:
            return refuse("test-list", "items is empty; list at least one test")
        covered = {i for spec in items for i in spec.get("criteria") or []}
        missing = [i for i in range(criteria_count) if i not in covered]
        if missing:
            return refuse("criteria-coverage",
                          f"acceptance criteria {missing} are covered by no item; "
                          "every criterion needs at least one test-list item")
    elif mode == "review-fix":
        if not items and not directives:
            return refuse("test-list", "the review asked for changes; list items or give directives")
        blob = "\n".join([s["text"] + "\n" + s["source"] for s in items] + directives)
        unanswered = [f for f in _finding_ids(tdd)
                      if re.search(rf"(?<![\w-]){re.escape(f)}(?![\w-])", blob) is None]
        if unanswered:
            return refuse("finding-coverage",
                          f"review findings {unanswered} appear in no item text, source or directive")
    added = [model.new_item(tdd, _spec(spec)) for spec in items]
    tdd["gaps"] = list(args.get("gaps") or [])
    tdd["directives"] = directives
    tdd["no_cheat"] = False
    tdd["current"] = None
    tdd["step"] = "red" if added or any(i["status"] == "todo" for i in tdd["items"]) else "refactor"
    _accepted(tdd, "analyzer")
    if run["phase"] == "ready":
        model.transition(run, "tdd")
    runner.save("tdd-list", {"items": [i["id"] for i in added], "gaps": tdd["gaps"],
                             "directives": directives, "mode": mode})
    return (f"test list ready: {len(added)} items, {len(tdd['gaps'])} gaps, "
            f"{len(directives)} directives; next step {tdd['step']}")


# ---------------------------------------------------------------------- red

_RED_ARGS = ("item_id", "test_file", "test_name", "last_assertion_line", "defeats")
REDUNDANT_PREFIX = "redundant:"


def _normalized_text(text):
    """An item text as the redundancy check compares it: the part before the
    reason, lowercase, whitespace collapsed."""
    return _one_line(text.split(" (")[0]).lower()


def redundant_items(tdd):
    """The item texts the analyzer pruned, from its `redundant: <text>
    (<why>)` gaps."""
    return [entry[len(REDUNDANT_PREFIX):].split(" (")[0].strip()
            for entry in tdd.get("gaps") or []
            if isinstance(entry, str) and entry.startswith(REDUNDANT_PREFIX)]


def _pruned_note(tdd, adds):
    """A prefix for a refusal when an added item repeats a case the analyzer
    pruned as redundant, else ''."""
    pruned = {_normalized_text(r) for r in redundant_items(tdd)}
    repeats = [a["text"] for a in adds if _normalized_text(a["text"]) in pruned]
    if not repeats:
        return ""
    return ("analyzer pruned this as redundant; add it only if the current code fails it "
            f"({'; '.join(repeats)}). ")


def _red_checks(tdd, args, output, code, ref, adds_items=False):
    """(check, message) for the first failed verification of the red run,
    else None."""
    if code == 0 and not _timed_out(ref):
        if adds_items:
            return ("red-tautology", "tautology: the new test passes against the current "
                    "production code, which already handles this case; the test proves "
                    "nothing. Call no_cheat_left if no other case fails unaided")
        return ("red-tautology", "tautology: the new test passes, so it proves nothing; "
                "a proper red fails at its assertion")
    if _timed_out(ref):
        return "red-timeout", "the test command timed out; a red test fails fast"
    if not re.search(tdd["assertion_pattern"], output):
        return ("red-assertion", f"the failure does not match assertion_pattern "
                f"/{tdd['assertion_pattern']}/: an exception, compile or import error "
                "is not a proper red")
    name = re.escape(os.path.basename(args["test_file"]))
    line = args["last_assertion_line"]
    if not re.search(rf"""{name}(?::|["']?,\s*line\s+){line}(?!\d)""", output):
        return ("red-location", f"the output never shows a failure at "
                f"{os.path.basename(args['test_file'])}:{line}; "
                "the test must fail at its last assertion")
    pattern = tdd.get("failure_count_pattern")
    if pattern:
        match = re.search(pattern, output)
        if not match or match.group(1) != "1":
            found = match.group(1) if match else "no match"
            return ("red-count", f"exactly one test must fail; failure_count_pattern gave {found}")
    return None


def red(home, run, role, session, args, *, runner):
    tdd = _tdd(run, "red")
    wt = _at_last_good(run, tdd, require_clean=False)
    base = tdd["last_good_commit"]
    test_globs = tdd["test_globs"]
    adds = [_spec(s) for s in args.get("add_items") or []]

    def refuse(check, message, **kw):
        return _refuse(run, runner, "red", "red-failed", check, message,
                       item=args["item_id"], **kw)

    bad = _bad_specs(adds, len(run.get("acceptance_criteria") or []))
    if bad:
        return refuse("red-item", bad)
    if args["item_id"] == "new":
        if not adds:
            return refuse("red-item", "item_id 'new' needs a non-empty add_items")
        target, text = None, adds[0]["text"]
    else:
        target = next((i for i in tdd["items"] if i["id"] == args["item_id"]), None)
        if target is None or target["status"] != "todo":
            todo = ", ".join(i["id"] for i in tdd["items"] if i["status"] == "todo") or "none"
            return refuse("red-item", f"item_id {args['item_id']} is not an unchecked item "
                                      f"(unchecked: {todo})")
        text = target["text"]

    dirty = gitio.dirty_paths(wt)
    tests = [p for p in dirty if globs.match(p, test_globs)]
    test_file = os.path.normpath(args["test_file"])
    if not tests:
        return refuse("red-test-file", "no test file changed; red adds a test")
    if test_file not in tests:
        return refuse("red-test-file", f"test_file {args['test_file']} is not among the changed "
                                       f"test files: {', '.join(tests)}")
    created = set(tdd.get("created_tests") or [])
    existing = [p for p in tests
                if (p not in created and gitio.exists_at(wt, base, p))
                or not os.path.exists(os.path.join(wt, p))]
    if existing:
        return refuse("red-existing-test", "red only creates test files; these existing tests "
                                           f"are read-only to it: {', '.join(existing)}")
    outside = model.outside_touches(run, dirty)
    if outside:
        return refuse("red-touches", f"changes outside the task's touches: {', '.join(outside)}")
    protected = [p for p in dirty if model.under(p, run.get("protected_paths") or [])]
    if protected:
        return refuse("red-protected", f"changes to protected paths: {', '.join(protected)}")
    snapshot = gitio.commit_all(wt, f"tdd({run['task_id']}): red: {_one_line(text)}")
    production = [(p, n) for p, n in gitio.diff_numstat(wt, base, snapshot)
                  if not globs.match(p, test_globs)]
    pruned_note = _pruned_note(tdd, adds)
    if adds and production:
        return refuse("red-stub", f"{pruned_note}a test that adds items may not change non-test "
                                  f"files ({', '.join(p for p, _ in production)}): it must fail "
                                  "against the current production code, not against a stub")
    stub = sum(n for _, n in production)
    if stub > tdd["red_stub_max_lines"]:
        return refuse("red-stub", f"red added {stub} lines to non-test files; the limit is "
                                  f"{tdd['red_stub_max_lines']} (a stub, not an implementation)")

    code, output, ref = runner.run_on_commit(
        run, snapshot, tdd["test_command"], _evidence_name(tdd, "red"))
    model.bump_op(run)
    failed = _red_checks(tdd, args, output, code, ref, adds_items=bool(adds))
    if failed:
        message = pruned_note + failed[1] if failed[0] == "red-tautology" else failed[1]
        return refuse(failed[0], message, exit_code=code, output=output, evidence=ref)

    if target is None:
        target = model.new_item(tdd, adds[0])
        adds = adds[1:]
    for spec in adds:
        model.new_item(tdd, spec)
    target.update(status="red", test_file=test_file, test_name=args["test_name"],
                  defeats=args["defeats"])
    gitio.move_branch_to(wt, snapshot)
    tdd["created_tests"] = sorted(created | {p for p in tests if not gitio.exists_at(wt, base, p)})
    tdd["locked"] = gitio.blob_map(wt, snapshot, lambda p: globs.match(p, test_globs))
    tdd["red_commit"] = tdd["last_good_commit"] = snapshot
    tdd["red"] = {"item_id": target["id"], "test_file": test_file,
                  "test_name": args["test_name"],
                  "last_assertion_line": args["last_assertion_line"],
                  "commit": snapshot, "output": ref}
    tdd["current"] = tdd["last_item"] = target["id"]
    tdd["no_cheat"] = False
    tdd["step"] = "green"
    tdd["history"].append({
        "kind": "cycle", "cycle": tdd["cycle"] + 1, "item_id": target["id"], "open": True,
        "red": ref, "green_tests": None, "green_lint": None,
        "refactor_tests": None, "refactor_lint": None,
        "commits": {"red": snapshot, "green": None, "refactor": None}})
    _accepted(tdd, "red")
    runner.save("tdd-red", {"commit": snapshot, "item": target["id"], "evidence": ref})
    return f"red verified for {target['id']}: commit {snapshot[:12]}"


def no_cheat_left(home, run, role, session, args, *, runner):
    tdd = _tdd(run, "red")
    todo = [i["id"] for i in tdd["items"] if i["status"] == "todo"]
    if todo:
        return _refuse(run, runner, "red", "red-failed", "no-cheat-left",
                       f"unchecked items remain ({', '.join(todo)}); "
                       "no_cheat_left is valid only when none do")
    tdd["no_cheat"] = True
    tdd["current"] = None
    tdd["step"] = "final"
    _accepted(tdd, "red")
    runner.save("tdd-no-cheat", {"reason": args["reason"]})
    return "no cheat left: the final run is next"


# ------------------------------------------------------ green and refactor

def _open_entry(tdd):
    if tdd["history"] and tdd["history"][-1].get("open"):
        return tdd["history"][-1]
    entry = {"kind": "repair", "cycle": tdd["cycle"], "item_id": tdd.get("last_item"),
             "open": True, "red": None, "green_tests": None, "green_lint": None,
             "refactor_tests": None, "refactor_lint": None,
             "commits": {"red": None, "green": None, "refactor": None}}
    tdd["history"].append(entry)
    return entry


def _item_text(tdd):
    item_id = tdd.get("current") or tdd.get("last_item")
    item = next((i for i in tdd["items"] if i["id"] == item_id), None)
    return item["text"] if item else "repair"


def _apply_step(run, runner, step, patch):
    """Apply a blind role's patch, check it, and leave the commit in place
    on the branch. Returns (commit, refs, changed), or the pause message when
    a refusal pushed the run past its attempt limit (any other refusal
    raises). A rejected patch leaves the worktree at the last good commit."""
    tdd = run["tdd"]
    event = f"tdd-{step}-failed"
    wt = _at_last_good(run, tdd, require_clean=True)
    base = tdd["last_good_commit"]
    item = tdd.get("current") or tdd.get("last_item")

    def reject(check, message, **kw):
        gitio.reset_hard(wt, base)
        return _refuse(run, runner, step, event, check, message, item=item, **kw)

    try:
        gitio.apply_patch(wt, patch)
    except GitError as exc:
        return reject("patch-apply", str(exc), tail=[str(exc)[:300]])
    dirty = gitio.dirty_paths(wt)
    if not dirty:
        return None, {"tests": None, "lint": None}, False
    touched = [p for p in dirty if globs.match(p, tdd["test_globs"])]
    if touched:
        return reject("tests-touched", f"{step} must not touch tests; refused paths: "
                                       f"{', '.join(touched)}")
    protected = [p for p in dirty if model.under(p, run.get("protected_paths") or [])
                 or p.split("/")[0] == ".git"]
    if protected:
        return reject("protected", f"{step} must not touch protected paths: {', '.join(protected)}")
    outside = model.outside_touches(run, dirty)
    if outside:
        return reject("touches", f"changes outside the task's touches: {', '.join(outside)}")
    commit = gitio.commit_all(wt, f"tdd({run['task_id']}): {step}: {_one_line(_item_text(tdd))}")
    failed, refs = _run_checks(run, tdd, runner, commit, step)
    if failed:
        check, code, output, ref = failed
        lines = _production_lines(output, tdd["test_globs"]) if check == "lint" \
            else _failure_lines(output, tdd)
        if step == "refactor":
            lines = ["red tests or lint failed after your refactor"] + (
                lines if check == "lint" else [])
        return reject(check, f"{check} failed on the {step} commit", exit_code=code,
                      tail=lines, evidence=ref)
    gitio.move_branch_to(wt, commit)
    return commit, refs, True


def _changed_lines(wt, a, b, test_globs):
    """Production lines added plus deleted between two commits. A binary
    file counts as unboundedly many."""
    out = gitio.git_ok(wt, "diff", "--numstat", "-z", "--no-renames", a, b, strip=False)
    total = 0
    for record in out.split("\0"):
        if record:
            added, deleted, path = record.split("\t", 2)
            if not globs.match(path, test_globs):
                total += int(added) + int(deleted) if added.isdigit() and deleted.isdigit() \
                    else 10 ** 9
    return total


def green(home, run, role, session, args, *, runner, patch):
    tdd = _tdd(run, "green")
    if not (patch or "").strip():
        return _refuse(run, runner, "green", "tdd-green-failed", "empty-patch",
                       "green_ready came with no patch; make the change, then call it")
    before = tdd["last_good_commit"]
    result = _apply_step(run, runner, "green", patch)
    if isinstance(result, str):
        return result
    commit, refs, changed = result
    if not changed:
        return _refuse(run, runner, "green", "tdd-green-failed", "empty-patch",
                       "the patch changes nothing")
    entry = _open_entry(tdd)
    entry.update(green_tests=refs["tests"], green_lint=refs["lint"])
    entry["commits"]["green"] = commit
    item = next((i for i in tdd["items"] if i["id"] == tdd.get("current")), None)
    if item and item["status"] == "red":
        item["status"] = "done"
    tdd["green_commit"] = tdd["last_good_commit"] = commit
    tdd["lines_since_refactor"] += _changed_lines(_worktree(run), before, commit,
                                                  tdd["test_globs"])
    _accepted(tdd, "green")
    message = f"green accepted: commit {commit[:12]}"
    lines, limit = tdd["lines_since_refactor"], tdd["refactor_skip_max_lines"]
    if tdd["directives"] or lines > limit:
        tdd["step"] = "refactor"
        runner.save("tdd-green", {"commit": commit, "item": tdd.get("current")})
        return message
    item_id = tdd.get("current")
    entry["open"] = False
    _close_cycle(tdd)
    runner.save("tdd-green", {"commit": commit, "item": item_id})
    runner.save("tdd-refactor-skipped", {"item": item_id, "lines_since_refactor": lines,
                                         "limit": limit})
    return _cycle_limit_pause(run, runner, tdd) or (
        f"{message}; refactor skipped ({lines} lines since the last refactor, limit {limit}); "
        f"next {tdd['step']}")


def _close_cycle(tdd):
    """Count a finished cycle and pick the next step."""
    tdd["red"] = None
    tdd["last_item"] = tdd.get("current") or tdd.get("last_item")
    tdd["current"] = None
    tdd["cycle"] += 1
    todo = any(i["status"] == "todo" for i in tdd["items"])
    tdd["step"] = "final" if tdd.get("no_cheat") and not todo else "red"
    _accepted(tdd, "refactor")


def _cycle_limit_pause(run, runner, tdd):
    """The pause message when the cycle limit stops the run before the next
    red, else None."""
    limit = run["budgets"]["cycle_limit"]
    if tdd["step"] != "red" or tdd["cycle"] < limit:
        return None
    question = (f"{tdd['cycle']} red-green-refactor cycles done; the cycle limit is {limit}. "
                "Answer to continue.")
    _pause(run, runner, question, "cycle-limit")
    return f"paused: {question}"


def refactor(home, run, role, session, args, *, runner, patch):
    tdd = _tdd(run, "refactor")
    patch = patch or ""
    if args["changed"] and not patch.strip():
        return _refuse(run, runner, "refactor", "tdd-refactor-failed", "empty-patch",
                       "refactor_done says changed, but there is no patch")
    commit, refs, changed = None, {"tests": None, "lint": None}, False
    if patch.strip():
        result = _apply_step(run, runner, "refactor", patch)
        if isinstance(result, str):
            return result
        commit, refs, changed = result
    entry = _open_entry(tdd)
    entry.update(refactor_tests=refs["tests"], refactor_lint=refs["lint"], open=False)
    if changed:
        entry["commits"]["refactor"] = commit
        tdd["last_good_commit"] = commit
    tdd["directives"] = []
    tdd["lines_since_refactor"] = 0
    _close_cycle(tdd)
    runner.save("tdd-refactor", {"commit": commit, "changed": changed, "cycle": tdd["cycle"]})
    return _cycle_limit_pause(run, runner, tdd) or (
        f"refactor accepted ({'commit ' + commit[:12] if changed else 'no changes'}); "
        f"next {tdd['step']}")


# -------------------------------------------------------------------- final

def _structural_problem(run, wt, tip, tdd):
    """(problem, security_paths) for the tip. problem is (check, message)
    for the first failure of: a review asking for changes answered with no
    new commit, locked tests differing, changes outside touches, changes to
    protected paths; else None."""
    review = model.changes_requested(run)
    if review is not None and tip == review["commit"]:
        return ("no-new-commit", f"the review asked for changes on {tip[:12]} and no new "
                "commit was made"), []
    current = gitio.blob_map(wt, tip, lambda p: globs.match(p, tdd["test_globs"]))
    if current != tdd["locked"]:
        changed = sorted(p for p in set(current) | set(tdd["locked"])
                         if current.get(p) != tdd["locked"].get(p))
        return ("tests-touched", f"tests differ from the red commit: {', '.join(changed)}"), []
    if not run.get("base_commit"):
        return None, []
    changed = gitio.changed_paths(wt, run["base_commit"], tip)
    outside = model.outside_touches(run, changed)
    if outside:
        return ("touches", f"changes outside the task's touches: {', '.join(outside)}"), []
    # Measured from where the tip leaves trunk, so a base that already
    # carries an unreviewed edit cannot hide it.
    fork = gitio.merge_base(wt, run["trunk_commit"], tip)
    changed = sorted(set(changed) | set(gitio.changed_paths(wt, fork, tip)))
    protected = [p for p in changed if model.under(p, run["protected_paths"])]
    if protected:
        return ("protected", f"changes to protected paths: {', '.join(protected)}"), []
    return None, [p for p in changed if model.under(p, profile_mod.security_roots(run))]


def final(home, run, *, runner):
    tdd = _tdd(run, "final")
    wt = _at_last_good(run, tdd, require_clean=True)
    tip = tdd["last_good_commit"]
    problem, sensitive = _structural_problem(run, wt, tip, tdd)
    if problem:
        return _refuse(run, runner, "green", "tdd-final-failed", problem[0], problem[1],
                       tail=[problem[1]], next_step="green")
    failed, refs = _run_checks(run, tdd, runner, tip, "final")
    if failed:
        check, code, output, ref = failed
        lines = _production_lines(output, tdd["test_globs"]) if check == "lint" \
            else _failure_lines(output, tdd)
        return _refuse(run, runner, "green", "tdd-final-failed", check,
                       f"{check} failed on the tip {tip[:12]}", exit_code=code, tail=lines,
                       evidence=ref, next_step="green")
    tdd["final"] = {"commit": tip, "tests": refs["tests"], "lint": refs["lint"],
                    "cycles": tdd["cycle"]}
    gate_mod.reset_for_new_candidate(run)
    model.void_reviews(run, f"superseded by {tip[:12]}")
    run["candidate"] = {"commit": tip, "staged_at": time.time()}
    run["security_paths_touched"] = sensitive
    tdd["step"] = "done"
    _accepted(tdd, "green")
    model.transition(run, "gates")
    runner.save("tdd-final", {"commit": tip, "cycles": tdd["cycle"],
                              "security_paths": sensitive})
    return f"final: candidate {tip[:12]} pinned after {tdd['cycle']} cycles"


# ----------------------------------------------------------------- contexts

def _read_report(ref):
    path = ref.get("path") if isinstance(ref, dict) else None
    if not path:
        return ""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def _current_item(tdd, fields):
    item = next((i for i in tdd["items"] if i["id"] == (tdd.get("current") or tdd.get("last_item"))), None)
    return {k: item[k] for k in fields} if item else None


def green_context(run):
    """What the blind green role sees: the item, never a test, and on a
    retry or a repair only sanitized failure lines."""
    tdd = run["tdd"]
    error = tdd.get("last_error")
    findings = None
    if tdd["mode"] == "repair" and tdd.get("findings"):
        findings = _failure_lines(_read_report(tdd["findings"].get("report")), tdd)
    return {
        "current_item": _current_item(tdd, ("id", "text", "needed")),
        "failure": list(error["tail"]) if error else None,
        "findings": findings,
        "last_error": {"check": error["check"], "message": error["message"],
                       "tail": list(error["tail"])} if error else None,
    }


def refactor_context(run):
    """What the blind refactor role sees: the production files changed in
    this run, their diff stat, and the structural directives."""
    tdd = run["tdd"]
    wt, base = run["worktree"], run.get("base_commit")
    files, stat = [], ""
    if base:
        files = [p for p in gitio.changed_paths(wt, base, tdd["last_good_commit"])
                 if not globs.match(p, tdd["test_globs"])]
        stat = gitio.diff_stat(wt, base, tdd["last_good_commit"], files) if files else ""
    error = tdd.get("last_error")
    return {
        "changed_files": files,
        "diff_stat": stat,
        "directives": list(tdd.get("directives") or []),
        "last_error": {"check": error["check"], "message": error["message"],
                       "tail": list(error["tail"])} if error else None,
    }


def red_context(run):
    """What the red role sees: the list, the next unchecked item (null: the
    devil pass, add a test or call no_cheat_left), and the raw last error."""
    tdd = run["tdd"]
    todo = next((i for i in tdd["items"] if i["status"] == "todo"), None)
    return {
        "current_item": ({k: todo[k] for k in ("id", "text", "kind", "source", "needed", "criteria")}
                         if todo else None),
        "items": [{k: i[k] for k in ("id", "text", "kind", "status", "criteria")}
                  for i in tdd["items"]],
        "criteria": list(run.get("acceptance_criteria") or []),
        "test_command": list(tdd["test_command"]),
        "test_globs": list(tdd["test_globs"]),
        "red_stub_max_lines": tdd["red_stub_max_lines"],
        "redundant": redundant_items(tdd),
        "files": gitio.tracked_files(run["worktree"])[:FILE_LIST_LIMIT],
        "last_error": tdd.get("last_error"),
    }
