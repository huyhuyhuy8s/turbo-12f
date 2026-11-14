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

A handler works on a lane: a tdd-shaped record, the worktree it lives in
and the paths it may write. The serial run is the implicit lane (the run's
`tdd` record and worktree, the task's touches as scope); a parallel lane
(`lane=` the lane record) has its own record, worktree and branch, and the
scope of its unit. A lane command never changes the run record: its saves go
through the CLI's lane save, which takes the run lock only to append the event.
"""

import copy
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


class Lane(NamedTuple):
    """What a handler works on. `owns` and `tests` are None for the serial
    run, whose scope is the task's touches."""
    tdd: dict
    worktree: str
    unit: str | None = None
    owns: list | None = None
    tests: list | None = None


LANE_SCOPE = "lane-scope"


def _lane(run, record=None):
    """The lane a handler works on: the lane `record`, or the serial run."""
    if record is None:
        return Lane(run["tdd"], run["worktree"])
    return Lane(record, record["worktree"], record["unit"], record["owns"], record["tests"])


def _outside_scope(lane, paths, writable):
    """The paths of `paths` a lane may not write; `writable` is the lane's
    owns, or owns and tests. Empty for the serial run, which has no scope of
    its own."""
    if lane.owns is None:
        return []
    return [p for p in paths if not model.under(p, writable)]


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

def _enter(run, record, *steps):
    """The lane a handler works on, after checking the run is in the tdd
    phase and the lane (the serial run's record, or the lane `record`) is at
    one of `steps`."""
    if record is None:
        tdd = run.get("tdd")
        if not tdd:
            raise StateError("this run has no tdd state; it was not prepared for the tdd lane")
        phase_ok = run["phase"] == "tdd" or (run["phase"] == "ready" and "analyzer" in steps)
        where = f"the run is in phase {run['phase']!r}, step {tdd['step']!r}"
    else:
        tdd = record
        phase_ok = run["phase"] == "tdd" and record["status"] == "active"
        where = (f"lane {record['unit']} is {record['status']!r}, step {tdd['step']!r}, "
                 f"in a run in phase {run['phase']!r}")
    if not phase_ok or tdd["step"] not in steps:
        raise StateError(f"this call needs tdd step {' or '.join(steps)}; {where}")
    return _lane(run, record)


def _worktree(lane):
    wt = lane.worktree
    if not gitio.is_git_repo(wt):
        raise StateError(f"worktree missing or not a git repo: {wt}")
    return wt


def _at_last_good(lane, require_clean):
    wt = _worktree(lane)
    last_good = lane.tdd["last_good_commit"]
    if gitio.head(wt) != last_good:
        raise StateError(
            f"worktree HEAD {gitio.head(wt)[:12]} is not the last good commit "
            f"{last_good[:12]}")
    if require_clean and not gitio.is_clean(wt):
        raise StateError("worktree has uncommitted changes")
    return wt


def _bump_op(run, lane):
    """One operation spent. A lane counts its own; the run's counter is the
    serial run's."""
    if lane.unit is None:
        model.bump_op(run)
    else:
        lane.tdd["ops_used"] = lane.tdd.get("ops_used", 0) + 1


def _accepted(tdd, step):
    tdd["attempts"][step] = 0
    tdd["last_error"] = None


def _pause(run, lane, runner, question, kind, extra=None):
    """Pause for a human: the run, or only the lane when a lane asked."""
    entry = {"question": question, "kind": kind, "asked_at": time.time(),
             "answer": None, "answered_at": None, **(extra or {})}
    if lane.unit is None:
        run["prior_phase"] = run["phase"]
        run.setdefault("escalations", []).append(entry)
        model.transition(run, "paused")
    else:
        lane.tdd.setdefault("escalations", []).append(entry)
        lane.tdd["status"] = "paused"
    runner.save("human-contact", {"question": question, "kind": kind})


def _refuse(run, lane, runner, step, event, check, message, *, exit_code=None, tail=None,
            output="", evidence=None, item=None, next_step=None):
    """Record a refused step and count the attempt. Raises StateError with
    `message`; past the attempt limit the run (or the lane) pauses for a
    human instead, and the pause message is returned."""
    tdd = lane.tdd
    if next_step:
        tdd["step"] = next_step
        if tdd.get("unit"):
            tdd["status"], tdd["finished_at"] = "active", None
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
        _pause(run, lane, runner, question, "attempt-limit")
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
    """A lane's evidence is named under its unit, `U1/tdd-001-red.txt`."""
    tdd["seq"] = tdd.get("seq", 0) + 1
    prefix = f"{tdd['unit']}/" if tdd.get("unit") else ""
    return f"{prefix}tdd-{tdd['seq']:03d}-{label}.txt"


def _timed_out(ref):
    return bool(((ref or {}).get("observation") or {}).get("timed_out"))


def _passed(code, ref):
    return code == 0 and not _timed_out(ref)


def _run_checks(run, lane, runner, commit, label):
    """Tests, then lint, on `commit` in the sandbox. The first failure
    ends it. Returns (failed, result): failed is (check, code, output, ref)
    or None; result holds each run's evidence ref."""
    tdd = lane.tdd
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
        _bump_op(run, lane)
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


GLOB_CHARS = frozenset("*?[]{}")


def _unit_path(raw):
    """(path, is_directory) of a unit path as stored: normalised, no
    trailing slash. None when it is not an exact file or a directory ending
    in `/`, relative, without glob characters."""
    if not model.is_worktree_path(raw) or GLOB_CHARS & set(raw) or os.path.normpath(raw) == ".":
        return None
    return os.path.normpath(raw), raw.endswith("/")


def _glob_may_match_in(directory, test_globs):
    """Whether some test glob can match a path inside `directory`: its fixed
    prefix and the directory's lie on one path."""
    prefix = directory + "/"
    for glob in test_globs:
        fixed = re.split(r"[*?\[]", glob, maxsplit=1)[0]
        if fixed.startswith(prefix) or prefix.startswith(fixed):
            return True
    return False


def _tests_problem(run, tdd, unit_id, path, is_dir):
    """A message when a unit's tests path cannot hold a test red creates."""
    wt, base, test_globs = run["worktree"], tdd["last_good_commit"], tdd["test_globs"]
    if is_dir:
        if not _glob_may_match_in(path, test_globs):
            return (f"unit {unit_id} tests directory {path}/ is not where test_globs "
                    f"{test_globs} put tests")
        existing = sorted(gitio.blob_map(
            wt, base, lambda p: model.under(p, [path]) and globs.match(p, test_globs)))
        if existing:
            return (f"unit {unit_id} tests directory {path}/ already holds test files at the "
                    f"base commit ({', '.join(existing[:3])}); red only creates test files")
    elif not globs.match(path, test_globs):
        return f"unit {unit_id} tests path {path} does not match test_globs {test_globs}"
    elif gitio.exists_at(wt, base, path):
        return (f"unit {unit_id} tests path {path} exists at the base commit; red only "
                "creates test files")
    return None


def _unit_problem(run, tdd, units, items):
    """((check, message), None) for the first rule a unit list breaks, else
    (None, units as stored: paths normalised). The rules are in DESIGN.md,
    "Parallel TDD lanes"."""
    ids = []
    used = {row["unit"] for row in run.get("lanes") or []}
    for unit in units:
        if not model.TASK_ID_RE.match(unit["id"]):
            return ("unit-id", f"unit id {unit['id']!r} must match {model.TASK_ID_RE.pattern}"), None
        if unit["id"] in ids:
            return ("unit-id", f"unit {unit['id']} is declared twice"), None
        if unit["id"] in used:
            return ("unit-id", f"unit {unit['id']} was already used by an earlier list of this "
                    "run; declare new unit ids"), None
        ids.append(unit["id"])
    stored, claimed = [], []
    for position, unit in enumerate(units):
        entry = {**unit, "owns": [], "tests": []}
        for key in ("owns", "tests"):
            for raw in unit[key]:
                parsed = _unit_path(raw)
                if parsed is None:
                    return ("unit-paths", f"unit {unit['id']} {key} entry {raw!r} must be an exact "
                            "file or a directory ending in '/', relative, with no glob "
                            "characters"), None
                entry[key].append(parsed[0])
                claimed.append((unit["id"], key, *parsed))
        if not entry["owns"] or not entry["tests"]:
            return ("unit-paths", f"unit {unit['id']} needs at least one owns and one tests path"), None
        if position and not unit["needed"].strip():
            return ("unit-needed", f"unit {unit['id']} is not the first unit and says nothing in "
                    "needed: say why it cannot share a lane with the others"), None
        stored.append(entry)
    for i, (unit_a, key_a, path_a, _) in enumerate(claimed):
        for unit_b, key_b, path_b, _ in claimed[i + 1:]:
            if unit_a != unit_b and model.paths_overlap(path_a, path_b):
                return ("unit-overlap", f"{unit_a} {key_a} {path_a!r} overlaps {unit_b} {key_b} "
                        f"{path_b!r}: no path may belong to two units; a shared file goes to "
                        "one unit and the other names it in after"), None
    for unit_id, key, path, is_dir in claimed:
        if key == "owns":
            outside = model.outside_touches(run, [path])
            if outside:
                return ("unit-touches", f"unit {unit_id} owns {path} outside the task's "
                        f"touches {run.get('touches')}"), None
        else:
            message = _tests_problem(run, tdd, unit_id, path, is_dir)
            if message:
                return ("unit-tests", message), None
    counts = {unit_id: 0 for unit_id in ids}
    for position, item in enumerate(items, 1):
        unit = item.get("unit")
        if unit not in counts:
            return ("unit-items", f"item {position} names unit {unit!r}; every item names one of "
                    f"the declared units {ids}"), None
        counts[unit] += 1
    empty = [unit_id for unit_id, count in counts.items() if not count]
    if empty:
        return ("unit-items", f"units {empty} have no item; every unit needs at least one"), None
    for unit in units:
        unknown = [a for a in unit["after"] if a not in counts]
        if unknown:
            return ("unit-after", f"unit {unit['id']} comes after unknown units {unknown}"), None
    waiting = {unit["id"]: set(unit["after"]) for unit in units}
    while waiting:
        ready = [u for u, deps in waiting.items() if not deps & waiting.keys()]
        if not ready:
            return ("unit-after", f"the after lists of {sorted(waiting)} form a cycle"), None
        for u in ready:
            del waiting[u]
    return None, stored


def _link_deps(run, lane, fork):
    """Link the task's `verify_deps` that exist in the run worktree into the
    lane worktree, as the run worktree has them, so a worker's session can run
    the suite there. The links never show as changes: each path goes in the
    repository's exclude file. A path tracked at the fork is refused, as in
    verification."""
    repo = run["worktree"]
    for dep in run.get("verify_deps") or []:
        source = os.path.join(repo, dep)
        if not os.path.exists(source):
            continue
        target = os.path.join(lane["worktree"], dep)
        if gitio.exists_at(repo, fork, dep) or os.path.lexists(target):
            raise StateError(f"declared dependency {dep} is tracked in the commit; "
                             "verify_deps must name untracked paths")
        os.makedirs(os.path.dirname(target), exist_ok=True)
        os.symlink(os.path.realpath(source), target)
        _exclude(repo, dep)


def _exclude(repo, path):
    """Add `path` to the exclude file the repository's worktrees share."""
    common = os.path.join(repo, gitio.git_ok(repo, "rev-parse", "--git-common-dir"))
    exclude = os.path.join(common, "info", "exclude")
    entry = "/" + path.strip("/")
    os.makedirs(os.path.dirname(exclude), exist_ok=True)
    text = ""
    if os.path.exists(exclude):
        with open(exclude, encoding="utf-8") as fh:
            text = fh.read()
    if entry not in text.splitlines():
        with open(exclude, "a", encoding="utf-8") as fh:
            fh.write(("" if not text or text.endswith("\n") else "\n") + entry + "\n")


def start_lane(home, run, lane):
    """Fork the lane's branch and worktree from the integration tip, and make
    the lane active. A stale branch or worktree left by a crashed analyzer
    step is replaced: nothing has used it."""
    fork = run["tdd"]["last_good_commit"]
    repo = run["worktree"]
    gitio.remove_worktree_force(repo, lane["worktree"])
    if gitio.branch_exists(repo, lane["branch"]):
        gitio.delete_branch(repo, lane["branch"])
    gitio.add_worktree(repo, lane["worktree"], lane["branch"], fork)
    _link_deps(run, lane, fork)
    lane.update(status="active", fork_commit=fork, last_good_commit=fork)


LANE_BUDGET = "lane-budget"
LANE_FRACTION = (3, 4)


def _share(weight, total, ops_left, cycles_left):
    """A lane's fixed share of what is left, in proportion to its item count.
    Lanes together get LANE_FRACTION of it; the rest, and the rounding, stays
    for integration and the cross-unit refactor. The core sees no spend in an
    act (its input is only the tool call), so cost is not carved here: the
    lane records its `cost_share`, the fraction of any spend limit it may use."""
    num, den = LANE_FRACTION
    op_limit, cycle_limit = ops_left * num * weight // (den * total), cycles_left * num * weight // (den * total)
    return {"op_limit": op_limit, "cycle_limit": cycle_limit,
            "share": {"op_limit": op_limit, "cycle_limit": cycle_limit},
            "cost_share": round(num * weight / (den * total), 4)}


def _fan_out(home, run, tdd, units, items, runner):
    """Make a lane per unit, start the ones with nothing to wait for, and
    leave the run's own record at step `lanes`. Splits what is left of the
    op limit and the cycle limit into the lanes' shares, a remainder kept for
    integration. Returns the lane records."""
    budgets = run["budgets"]
    ops_left = max(0, budgets["op_limit"] - budgets["ops_used"])
    cycles_left = max(0, budgets["cycle_limit"] - tdd["cycle"])
    total = len(items)
    lanes = []
    for unit in units:
        lane = model.new_lane(home, run, unit, "active" if not unit["after"] else "waiting")
        for raw in items:
            if raw["unit"] == unit["id"]:
                model.new_item(lane, _spec(raw))
        lane["budget"] = _share(len(lane["items"]), total, ops_left, cycles_left)
        if lane["status"] == "active":
            start_lane(home, run, lane)
        lanes.append(lane)
    task_id = run["task_id"]
    for lane in lanes:
        model.save_lane(home, lane, task_id)
    run["lanes"] = list(run.get("lanes") or []) + [model.lane_row(lane) for lane in lanes]
    tdd["reserve"] = {"op_limit": ops_left - sum(l["budget"]["op_limit"] for l in lanes),
                      "cycle_limit": cycles_left - sum(l["budget"]["cycle_limit"] for l in lanes)}
    tdd.setdefault("pool", {"op_limit": 0, "cycle_limit": 0})
    tdd["step"] = "lanes"
    return lanes


def analyze(home, run, role, session, args, *, runner, lane=None):
    if lane is not None:
        raise StateError("the analyzer does not run in a lane")
    tdd = _enter(run, None, "analyzer").tdd

    def refuse(check, message):
        return _refuse(run, _lane(run), runner, "analyzer", "tdd-list-failed", check, message)

    items = args["items"]
    units = args.get("units") or []
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
    # Without units, or with one, or when max_lanes is 1, the run stays serial
    # and the list is read as it always was.
    parallel = len(units) >= 2 and tdd.get("max_lanes", model.DEFAULT_MAX_LANES) >= 2
    declared = {u["id"] for u in units}
    if not parallel:
        unknown = sorted({i["unit"] for i in items if "unit" in i and i["unit"] not in declared})
        if unknown:
            return refuse("unit-items", f"items name units {unknown} that the list does not declare")
    else:
        problem, stored = _unit_problem(run, tdd, units, items)
        if problem:
            return refuse(*problem)
    tdd["gaps"] = list(args.get("gaps") or [])
    tdd["directives"] = directives
    tdd["no_cheat"] = False
    tdd["current"] = None
    if parallel:
        lanes = _fan_out(home, run, tdd, stored, items, runner)
        _accepted(tdd, "analyzer")
        if run["phase"] == "ready":
            model.transition(run, "tdd")
        added = [i["id"] for lane_ in lanes for i in lane_["items"]]
        runner.save("tdd-list", {"items": added, "gaps": tdd["gaps"],
                                 "directives": directives, "mode": mode})
        runner.save("tdd-fanout", {
            "max_lanes": tdd["max_lanes"], "fork_commit": tdd["last_good_commit"],
            "reserve": tdd["reserve"],
            "units": [{"unit": l["unit"], "owns": l["owns"], "tests": l["tests"],
                       "after": l["after"], "status": l["status"], "branch": l["branch"],
                       "budget": l["budget"],
                       "items": [i["id"] for i in l["items"]]} for l in lanes]})
        return (f"test list ready: {len(added)} items in {len(lanes)} lanes "
                f"({', '.join(l['unit'] for l in lanes if l['status'] == 'active')} started)")
    added = [model.new_item(tdd, _spec(spec)) for spec in items]
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
        hint = ("" if adds_items else
                f"; if the code under test does not exist yet, add the smallest stub "
                f"(at most {tdd['red_stub_max_lines']} lines outside the tests) so the "
                "test fails at its assertion")
        return ("red-assertion", f"the failure does not match assertion_pattern "
                f"/{tdd['assertion_pattern']}/: an exception, compile or import error "
                f"is not a proper red{hint}")
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


def red(home, run, role, session, args, *, runner, lane=None):
    ctx = _enter(run, lane, "red")
    tdd = ctx.tdd
    wt = _at_last_good(ctx, require_clean=False)
    base = tdd["last_good_commit"]
    test_globs = tdd["test_globs"]
    adds = [_spec(s) for s in args.get("add_items") or []]

    def refuse(check, message, **kw):
        return _refuse(run, ctx, runner, "red", "red-failed", check, message,
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
    if ctx.unit is not None:
        # Tests go under the unit's tests; a stub, the only production edit
        # red may make, under its owns.
        stray = [p for p in dirty
                 if not model.under(p, ctx.tests if p in tests else ctx.owns)]
        if stray:
            mine = sorted(p for p in created if model.under(p, ctx.tests))
            extend = (f"; you created {', '.join(mine)} earlier in this run and may add the "
                      "test there") if mine else ""
            return refuse(LANE_SCOPE, f"unit {ctx.unit} may write tests under "
                                      f"{', '.join(ctx.tests)} and a stub under "
                                      f"{', '.join(ctx.owns)}; changes outside: "
                                      f"{', '.join(stray)}{extend}")
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
    _bump_op(run, ctx)
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


def _lane_done(tdd):
    """A lane that has reached its own final waits for integrate; the time it
    got there orders the merges."""
    if tdd.get("unit") and tdd["step"] == "final":
        tdd["status"], tdd["finished_at"] = "done", time.time()


def no_cheat_left(home, run, role, session, args, *, runner, lane=None):
    ctx = _enter(run, lane, "red")
    tdd = ctx.tdd
    todo = [i["id"] for i in tdd["items"] if i["status"] == "todo"]
    if todo:
        return _refuse(run, ctx, runner, "red", "red-failed", "no-cheat-left",
                       f"unchecked items remain ({', '.join(todo)}); "
                       "no_cheat_left is valid only when none do")
    tdd["no_cheat"] = True
    tdd["current"] = None
    tdd["step"] = "final"
    _lane_done(tdd)
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


def _apply_step(run, ctx, runner, step, patch):
    """Apply a blind role's patch, check it, and leave the commit in place
    on the branch. Returns (commit, refs, changed), or the pause message when
    a refusal pushed the run past its attempt limit (any other refusal
    raises). A rejected patch leaves the worktree at the last good commit."""
    tdd = ctx.tdd
    event = f"tdd-{step}-failed"
    wt = _at_last_good(ctx, require_clean=True)
    base = tdd["last_good_commit"]
    item = tdd.get("current") or tdd.get("last_item")

    def reject(check, message, **kw):
        gitio.reset_hard(wt, base)
        return _refuse(run, ctx, runner, step, event, check, message, item=item, **kw)

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
    stray = _outside_scope(ctx, dirty, ctx.owns or [])
    if stray:
        return reject(LANE_SCOPE, f"{step} in {'unit ' + ctx.unit if ctx.unit else 'the integration green'} "
                                  f"may only change {', '.join(ctx.owns)}; "
                                  f"refused paths: {', '.join(stray)}")
    outside = model.outside_touches(run, dirty)
    if outside:
        return reject("touches", f"changes outside the task's touches: {', '.join(outside)}")
    commit = gitio.commit_all(wt, f"tdd({run['task_id']}): {step}: {_one_line(_item_text(tdd))}")
    failed, refs = _run_checks(run, ctx, runner, commit, step)
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


def green(home, run, role, session, args, *, runner, patch, lane=None):
    if lane is None and (run.get("tdd") or {}).get("step") == model.INTEGRATION_GREEN:
        return _integration_green(home, run, runner, patch)
    ctx = _enter(run, lane, "green")
    tdd = ctx.tdd
    if not (patch or "").strip():
        return _refuse(run, ctx, runner, "green", "tdd-green-failed", "empty-patch",
                       "green_ready came with no patch; make the change, then call it")
    before = tdd["last_good_commit"]
    result = _apply_step(run, ctx, runner, "green", patch)
    if isinstance(result, str):
        return result
    commit, refs, changed = result
    if not changed:
        return _refuse(run, ctx, runner, "green", "tdd-green-failed", "empty-patch",
                       "the patch changes nothing")
    entry = _open_entry(tdd)
    entry.update(green_tests=refs["tests"], green_lint=refs["lint"])
    entry["commits"]["green"] = commit
    item = next((i for i in tdd["items"] if i["id"] == tdd.get("current")), None)
    if item and item["status"] == "red":
        item["status"] = "done"
    tdd["green_commit"] = tdd["last_good_commit"] = commit
    tdd["lines_since_refactor"] += _changed_lines(_worktree(ctx), before, commit,
                                                  tdd["test_globs"])
    _accepted(tdd, "green")
    if ctx.unit:
        tdd["reopened"] = None
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
    return _cycle_limit_pause(run, ctx, runner) or (
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
    _lane_done(tdd)
    _accepted(tdd, "refactor")


def cycle_limit(run, tdd):
    """A lane's cycle limit is its own budget share; without one, the run's."""
    return (tdd.get("budget") or {}).get("cycle_limit", run["budgets"]["cycle_limit"])


def _cycle_limit_pause(run, ctx, runner):
    """The pause message when the cycle limit stops the run (or the lane)
    before the next red, else None. A lane with a share of the cycles pauses
    as `lane-budget`, naming its unit."""
    tdd = ctx.tdd
    limit = cycle_limit(run, tdd)
    if tdd["step"] != "red" or tdd["cycle"] < limit:
        return None
    if (tdd.get("budget") or {}).get("cycle_limit") is not None:
        question = (f"lane {ctx.unit} used its share of {limit} red-green-refactor cycles "
                    f"({tdd['cycle']} done). It continues when a merging lane returns cycles "
                    "to the pool, or when a human raises the share.")
        _pause(run, ctx, runner, question, LANE_BUDGET, {"dimension": "cycles"})
        return f"paused: {question}"
    question = (f"{tdd['cycle']} red-green-refactor cycles done; the cycle limit is {limit}. "
                "Answer to continue.")
    _pause(run, ctx, runner, question, "cycle-limit")
    return f"paused: {question}"


def lane_budget_pause(run, lane_record, runner):
    """Pause a lane that has spent its share of operations, naming its unit.
    Returns the pause message, or None while it has some left (or no more
    work to do: a lane at its final needs only integrate)."""
    limit = (lane_record.get("budget") or {}).get("op_limit")
    if (limit is None or lane_record["status"] != "active" or lane_record["step"] == "final"
            or lane_record["ops_used"] < limit):
        return None
    question = (f"lane {lane_record['unit']} used its share of {limit} operations "
                f"({lane_record['ops_used']} spent). It continues when a merging lane returns "
                "operations to the pool, or when a human raises the share.")
    _pause(run, _lane(run, lane_record), runner, question, LANE_BUDGET, {"dimension": "ops"})
    return f"paused: {question}"


def refactor(home, run, role, session, args, *, runner, patch, lane=None):
    ctx = _enter(run, lane, "refactor")
    tdd = ctx.tdd
    patch = patch or ""
    if args["changed"] and not patch.strip():
        return _refuse(run, ctx, runner, "refactor", "tdd-refactor-failed", "empty-patch",
                       "refactor_done says changed, but there is no patch")
    commit, refs, changed = None, {"tests": None, "lint": None}, False
    if patch.strip():
        result = _apply_step(run, ctx, runner, "refactor", patch)
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
    return _cycle_limit_pause(run, ctx, runner) or (
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


def _unanswered_review(run, tip):
    """The question a human gets when the tip still carries the commit a review
    asked changes on: the refusal and the review's report, verbatim."""
    review = model.changes_requested(run)
    with open(review["report"]["path"], encoding="utf-8", errors="replace") as fh:
        report = fh.read()
    return (f"refusing final: the {review['role']} review ({review['report']['name']}) asked "
            f"for changes on {tip[:12]} and no new commit was made; answer its findings with "
            f"new red-green items or refactor directives.\n\nReview report:\n\n{report}")


def final(home, run, *, runner):
    ctx = _enter(run, None, "final")
    tdd = ctx.tdd
    wt = _at_last_good(ctx, require_clean=True)
    tip = tdd["last_good_commit"]
    problem, sensitive = _structural_problem(run, wt, tip, tdd)
    if problem:
        try:
            return _refuse(run, ctx, runner, "green", "tdd-final-failed", problem[0], problem[1],
                           tail=[problem[1]], next_step="green")
        except StateError:
            # A review that asked for changes and a tip that did not move cannot
            # be repaired by retrying the step: put the report in front of a human.
            if problem[0] == "no-new-commit":
                _pause(run, ctx, runner, _unanswered_review(run, tip), "no-new-commit")
            raise
    failed, refs = _run_checks(run, ctx, runner, tip, "final")
    if failed:
        check, code, output, ref = failed
        lines = _production_lines(output, tdd["test_globs"]) if check == "lint" \
            else _failure_lines(output, tdd)
        return _refuse(run, ctx, runner, "green", "tdd-final-failed", check,
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


# -------------------------------------------------------------- integration

def _refuse_message(unit, route):
    return f"integration of {unit} failed ({route})"


def _integrable(home, run, lane_record):
    """Raise unless `lane_record` may integrate now: the run is at step
    `lanes`, the lane reached its final, and it is the lane that finished
    first (the lowest unit id among lanes that finished together)."""
    unit = lane_record["unit"]
    if run["phase"] != "tdd":
        raise StateError(f"integrate needs the run in phase 'tdd', it is {run['phase']!r}")
    step = run["tdd"]["step"]
    if step != "lanes":
        raise StateError(f"the run is at tdd step {step!r}; lanes integrate only while it is at "
                         "'lanes'" + (" (the integration green repairs a merge first)"
                                      if step == model.INTEGRATION_GREEN else ""))
    if lane_record["status"] != "done" or lane_record["step"] != "final":
        raise StateError(f"lane {unit} is {lane_record['status']!r} at step "
                         f"{lane_record['step']!r}; it integrates once it reaches its final")
    head = model.integrate_head(model.list_lanes(home, run["task_id"]))
    if head != unit:
        raise StateError(f"lane {head} finished first; integrate it before {unit}")


def _lane_problem(run, ctx):
    """(check, message) for the first thing wrong with a lane's tip, else
    None: locked tests differing, a path outside the unit's owns and tests,
    outside the task's touches, or protected."""
    tdd, tip = ctx.tdd, ctx.tdd["last_good_commit"]
    current = gitio.blob_map(ctx.worktree, tip, lambda p: globs.match(p, tdd["test_globs"]))
    if current != tdd["locked"]:
        changed = sorted(p for p in set(current) | set(tdd["locked"])
                         if current.get(p) != tdd["locked"].get(p))
        return "tests-touched", f"tests differ from the red commit: {', '.join(changed)}"
    changed = gitio.changed_paths(ctx.worktree, tdd.get("scope_base") or tdd["fork_commit"], tip)
    stray = _outside_scope(ctx, changed, ctx.owns + ctx.tests)
    if stray:
        return LANE_SCOPE, (f"unit {ctx.unit} changed paths outside its owns and tests: "
                            f"{', '.join(stray)}")
    outside = model.outside_touches(run, changed)
    if outside:
        return "touches", f"changes outside the task's touches: {', '.join(outside)}"
    protected = [p for p in changed if model.under(p, run.get("protected_paths") or [])]
    if protected:
        return "protected", f"changes to protected paths: {', '.join(protected)}"
    return None


def lane_final(home, run, lane_record, *, runner):
    """Integrate step 1, on the lane's own lock: the lane's red tests and
    lint on its tip, as `final` does, within its scope. A failure reopens the
    lane at green. Returns None when the lane may merge, or the pause message
    when the refusal was its last attempt."""
    _integrable(home, run, lane_record)
    ctx = _lane(run, lane_record)
    _at_last_good(ctx, require_clean=True)
    tip = lane_record["last_good_commit"]
    problem = _lane_problem(run, ctx)
    if problem:
        return _refuse(run, ctx, runner, "green", "tdd-final-failed", problem[0], problem[1],
                       tail=[problem[1]], next_step="green")
    failed, refs = _run_checks(run, ctx, runner, tip, "final")
    if failed:
        check, code, output, ref = failed
        lines = _production_lines(output, lane_record["test_globs"]) if check == "lint" \
            else _failure_lines(output, lane_record)
        return _refuse(run, ctx, runner, "green", "tdd-final-failed", check,
                       f"{check} failed on the tip {tip[:12]}", exit_code=code, tail=lines,
                       evidence=ref, next_step="green")
    lane_record["final"] = {"commit": tip, "tests": refs["tests"], "lint": refs["lint"],
                            "cycles": lane_record["cycle"]}
    runner.save("tdd-lane-final", {"commit": tip, "cycles": lane_record["cycle"]})
    return None


def _integration_lane(run, owns=None):
    """The implicit lane of the integration worktree: the run's record and
    worktree, and (for the integration green) the union of the owns of the
    lanes in the merge as its scope."""
    return Lane(run["tdd"], run["worktree"], None, owns)


def _merge_into(run, lane_record, pre):
    """`git merge --no-ff` of the lane's branch into the integration branch.
    Returns (merge commit, []) or, on a textual conflict, (None, conflicted
    files) with the merge aborted and the branch back at `pre`."""
    wt = run["worktree"]
    obs = gitio.git(wt, "merge", "--no-ff", "-m",
                    f"tdd({run['task_id']}): merge {lane_record['unit']}", lane_record["branch"])
    if obs["exit_code"] == 0 and not obs["timed_out"]:
        return gitio.head(wt), []
    files = [f for f in gitio.git(wt, "diff", "--name-only", "--diff-filter=U")["stdout"].split("\n")
             if f]
    gitio.git(wt, "merge", "--abort")
    gitio.reset_hard(wt, pre)
    return None, files or [(obs["stderr"] or obs["stdout"]).strip()[:200]]


def _conflict(run, lane_record, pre, files, runner):
    """A textual conflict means the disjointness check has a bug: the run
    pauses (`integration-conflict`) and the core never retries."""
    unit, tdd = lane_record["unit"], run["tdd"]
    tdd["integration_failures"].append({"unit": unit, "route": "conflict", "failed": [],
                                        "check": "merge", "pre_merge": pre})
    runner.save("tdd-integration-conflict", {"unit": unit, "files": files, "pre_merge": pre})
    question = (f"merging lane {unit} conflicted in {', '.join(files)}; units own disjoint "
                "files, so the overlap check has a bug. A human must look.")
    _pause(run, _integration_lane(run), runner, question, "integration-conflict")
    return f"paused: {question}"


def _blamed_units(output, check, lanes):
    """The units of `lanes` a failed run of the merged commit names: by a test
    name or test file of the lane in the output, and for lint by a path under
    the lane's owns or tests. Empty when the output names none of them."""
    blamed = []
    for lane in sorted(lanes, key=lambda l: l["unit"]):
        names = _test_names(lane)
        files = [os.path.basename(i["test_file"]) for i in lane["items"] if i.get("test_file")]
        hit = any(_names_test(n, output) for n in names) or any(f in output for f in files)
        if check == "lint":
            hit = hit or any(re.search(rf"(?<![\w.-]){re.escape(p.strip('/'))}", output)
                             for p in lane["owns"] + lane["tests"])
        if hit:
            blamed.append(lane["unit"])
    return blamed


def _count_repair(run, runner, event, detail, lanes):
    """One repair used by an integration failure: record the failure, and
    past the repair cap pause the run as a gate failure does."""
    budgets = run["budgets"]
    budgets["repairs_used"] += 1
    runner.save(event, {**detail, "repairs_used": budgets["repairs_used"],
                        "repair_cap": budgets["repair_cap"]}, lanes=lanes)
    if budgets["repairs_used"] <= budgets["repair_cap"]:
        return None
    question = (f"repair cap exceeded: {budgets['repairs_used']} repairs used, "
                f"{budgets['repair_cap']} allowed; the merge of lane {detail['unit']} failed "
                "its tests. Answer with guidance to continue.")
    _pause(run, _integration_lane(run), runner, question, "repair-cap")
    return f"paused: {question}"


def _reopen_lane(lane_record, merged, merge_commit, pre, blamed, error):
    """Only the merging lane's tests failed: reopen it at green, its branch
    reset to the merge result so green sees the other units' code."""
    wt = lane_record["worktree"]
    gitio.reset_hard(wt, merge_commit)
    created = set()
    for lane in merged:
        created.update(lane.get("created_tests") or [])
    lane_record.update(
        last_good_commit=merge_commit, scope_base=merge_commit, step="green", status="active",
        finished_at=None, last_error=error,
        created_tests=sorted(created | set(lane_record.get("created_tests") or [])),
        locked=gitio.blob_map(wt, merge_commit,
                              lambda p: globs.match(p, lane_record["test_globs"])),
        reopened={"merge_commit": merge_commit, "pre_merge": pre, "failed": blamed,
                  "tail": list(error["tail"])})
    lane_record["attempts"]["green"] = 0


def _start_integration_green(run, lane_record, merged, merge_commit, pre, blamed, error):
    """Leave the run at step `integration-green`: the worktree is on the merge
    result, which is not yet recorded as merged, and the green may write the
    owns of every lane in the merge."""
    tdd = run["tdd"]
    # The worktree holds the pending lane's tests now: resume checks them as locked.
    tdd["locked"] = {**tdd["locked"], **lane_record["locked"]}
    tdd["red_commit"] = tdd.get("red_commit") or merge_commit
    tdd.update(
        step=model.INTEGRATION_GREEN, last_good_commit=merge_commit, current=None, red=None,
        last_error=error,
        pending_merge={"unit": lane_record["unit"], "pre_merge": pre, "merge_commit": merge_commit,
                       "units": sorted(l["unit"] for l in merged),
                       "owns": sorted({p for l in merged for p in l["owns"]}),
                       "failed": blamed})
    tdd["attempts"]["green"] = 0


def _merged_lanes(home, run, lane_record):
    """Every lane record of the run, the merging one as given."""
    lanes = {l["unit"]: l for l in model.list_lanes(home, run["task_id"])}
    lanes[lane_record["unit"]] = lane_record
    return lanes


def integrate(home, run, lane_record, *, runner):
    """Integrate steps 2 to 5, under the integration lock: merge the lane's
    branch into the integration branch, run every red test and lint on the
    merge commit, and on a pass record it; on a failure route the repair by
    blame (own tests: the lane reopens at green; a merged lane's test:
    integration green). `runner` runs commands on the integration worktree and
    saves through the run lock, with the lane records it names."""
    _integrable(home, run, lane_record)
    tdd, wt, unit = run["tdd"], run["worktree"], lane_record["unit"]
    pre = tdd["last_good_commit"]
    if gitio.head(wt) != pre:
        gitio.reset_hard(wt, pre)
    tdd["integrations"] = tdd.get("integrations", 0) + 1
    merge_commit, files = _merge_into(run, lane_record, pre)
    if merge_commit is None:
        return _conflict(run, lane_record, pre, files, runner)
    lanes = _merged_lanes(home, run, lane_record)
    merged = [l for l in lanes.values() if l["status"] == "merged"] + [lane_record]
    failed, refs = _run_checks(run, _integration_lane(run), runner, merge_commit, "integrate")
    if not failed:
        return _complete_merge(home, run, lane_record, lanes, merge_commit, refs, runner, "merge")
    check, code, output, evidence_ref = failed
    blamed = _blamed_units(output, check, merged)
    combined = {"items": [i for l in merged for i in l["items"]], "red": None,
                "test_globs": tdd["test_globs"], "assertion_pattern": tdd["assertion_pattern"]}
    tail = _production_lines(output, tdd["test_globs"]) if check == "lint" \
        else _failure_lines(output, combined)
    error = {"check": check, "message": f"{check} failed on the merged commit {merge_commit[:12]}",
             "exit_code": code, "tail": tail}
    route = "own-tests" if blamed == [unit] else "integration-green"
    tdd["integration_failures"].append({"unit": unit, "route": route, "failed": blamed,
                                        "check": check, "merge_commit": merge_commit,
                                        "pre_merge": pre})
    if route == "own-tests":
        gitio.reset_hard(wt, pre)
        _reopen_lane(lane_record, merged, merge_commit, pre, blamed, error)
        outcome = f"lane {unit} reopened at green"
    else:
        _start_integration_green(run, lane_record, merged, merge_commit, pre, blamed, error)
        outcome = "integration green next"
    detail = {"unit": unit, "route": route, "check": check, "failed_lanes": blamed,
              "evidence": evidence_ref,
              "merge_commit": merge_commit, "rolled_back_to": pre if route == "own-tests" else None,
              "next": outcome}
    paused = _count_repair(run, runner, "tdd-integration-failed", detail, [lane_record])
    return paused or f"{_refuse_message(unit, route)}: {check} failed on the merge; {outcome}"


def scope_refused_after_integration(lane_record):
    """True when a reopened lane's green was just refused for writing outside
    its owns: the fix belongs in another unit."""
    error = lane_record.get("last_error") or {}
    return (bool(lane_record.get("reopened")) and lane_record["status"] == "active"
            and lane_record["step"] == "green" and error.get("check") == LANE_SCOPE)


def to_integration_green(home, run, lane_record, *, runner):
    """The reopened lane's green was refused for writing outside its owns:
    skip its remaining attempts and merge it again, onto the current
    integration tip, for the integration green. Returns what happened."""
    tdd, wt, unit = run["tdd"], run["worktree"], lane_record["unit"]
    if run["phase"] != "tdd" or tdd["step"] != "lanes":
        raise StateError(f"the run is at tdd step {tdd['step']!r}; it cannot take an "
                         "integration green now")
    reopened = lane_record["reopened"]
    pre = tdd["last_good_commit"]
    if gitio.head(wt) != pre:
        gitio.reset_hard(wt, pre)
    merge_commit, files = _merge_into(run, lane_record, pre)
    if merge_commit is None:
        return _conflict(run, lane_record, pre, files, runner)
    lanes = _merged_lanes(home, run, lane_record)
    merged = [l for l in lanes.values() if l["status"] == "merged"] + [lane_record]
    error = {"check": "lane-scope", "message": "the failing merge needs a change outside "
             f"unit {unit}'s files", "exit_code": None, "tail": list(reopened["tail"])}
    lane_record.update(step="final", status="done", finished_at=time.time(), reopened=None)
    _start_integration_green(run, lane_record, merged, merge_commit, pre, reopened["failed"], error)
    for failure in reversed(tdd["integration_failures"]):
        if failure["unit"] == unit:
            failure.update(route="scope-refused", failed=reopened["failed"])
            break
    detail = {"unit": unit, "route": "scope-refused", "failed_lanes": reopened["failed"],
              "merge_commit": merge_commit, "next": "integration green next"}
    paused = _count_repair(run, runner, "tdd-integration-rerouted", detail, [lane_record])
    return paused or (f"lane {unit}'s green wrote outside its owns; integration green next "
                      f"on {merge_commit[:12]}")


def _integration_green(home, run, runner, patch):
    """The green role on the integration worktree, on the merge result: the
    same prompt, blindness and checks as a lane's green (every red test and
    lint on the commit), allowed to write the owns of every lane in the merge.
    Refusals count attempts; past the attempt limit the run pauses."""
    tdd = run["tdd"]
    if run["phase"] != "tdd":
        raise StateError(f"the run is in phase {run['phase']!r}, not 'tdd'")
    pending = tdd["pending_merge"]
    ctx = _integration_lane(run, list(pending["owns"]))
    if not (patch or "").strip():
        return _refuse(run, ctx, runner, "green", "tdd-green-failed", "empty-patch",
                       "green_ready came with no patch; make the change, then call it")
    result = _apply_step(run, ctx, runner, "green", patch)
    if isinstance(result, str):
        return result
    commit, refs, changed = result
    if not changed:
        return _refuse(run, ctx, runner, "green", "tdd-green-failed", "empty-patch",
                       "the patch changes nothing")
    entry = _open_entry(tdd)
    entry.update(green_tests=refs["tests"], green_lint=refs["lint"], open=False)
    entry["commits"]["green"] = commit
    tdd["green_commit"] = commit
    lane_record = model.load_lane(home, run["task_id"], pending["unit"])
    return _complete_merge(home, run, lane_record, _merged_lanes(home, run, lane_record), commit,
                           refs, runner, "integration-green")


def _grant(tdd, lane_record):
    """Give a lane paused on `lane-budget` what the pool has, up to its own
    share, and resume it. Only integrate calls this."""
    pending = model.open_escalation(lane_record)
    if lane_record["status"] != "paused" or not pending or pending.get("kind") != LANE_BUDGET:
        return None
    dimension = pending.get("dimension")
    key = "op_limit" if dimension == "ops" else "cycle_limit"
    amount = min(tdd["pool"][key], max(lane_record["budget"]["share"][key], 1))
    if amount <= 0:
        return None
    tdd["pool"][key] -= amount
    lane_record["budget"][key] += amount
    pending["answer"] = f"integrate granted {amount} {dimension} from the pool"
    pending["answered_at"] = time.time()
    lane_record["status"] = "done" if lane_record["step"] == "final" else "active"
    return {"unit": lane_record["unit"], "dimension": dimension, "amount": amount}


def _complete_merge(home, run, lane_record, lanes, commit, refs, runner, via):
    """Record a merge: the lane is merged at `commit`, the run's tdd record
    takes the lane's items, history and counters, what the lane left unspent
    returns to the pool (and is granted to lanes paused on `lane-budget`), the
    lanes whose `after` units are all merged start from the new tip, and when
    every lane is merged the run goes on to the cross-unit refactor or final."""
    tdd, unit = run["tdd"], lane_record["unit"]
    lane_record.update(status="merged", merged_commit=commit)
    order = tdd.setdefault("merge_order", [])
    order.append(unit)
    tdd["last_good_commit"] = tdd["red_commit"] = commit
    tdd["locked"] = {**tdd["locked"], **lane_record["locked"]}
    tdd["created_tests"] = sorted(set(tdd.get("created_tests") or [])
                                  | set(lane_record.get("created_tests") or []))
    tdd["items"] += copy.deepcopy(lane_record["items"])
    tdd["history"] += copy.deepcopy(lane_record["history"])
    tdd["cycle"] += lane_record["cycle"]
    tdd["attempts_peak"] = max(tdd.get("attempts_peak", 0), lane_record.get("attempts_peak", 0))
    tdd["lines_since_refactor"] += lane_record["lines_since_refactor"]
    run["budgets"]["ops_used"] += lane_record["ops_used"]
    tdd.setdefault("merges", []).append({"unit": unit, "commit": commit, "via": via,
                                         "tests": refs["tests"], "lint": refs["lint"]})
    tdd.update(pending_merge=None, step="lanes", last_error=None)
    tdd["attempts"]["green"] = 0
    budget = lane_record["budget"]
    pool = tdd.setdefault("pool", {"op_limit": 0, "cycle_limit": 0})
    pool["op_limit"] += max(0, budget["op_limit"] - lane_record["ops_used"])
    pool["cycle_limit"] += max(0, budget["cycle_limit"] - lane_record["cycle"])
    touched, started, grants = [lane_record], [], []
    for other in sorted(lanes.values(), key=lambda l: l["unit"]):
        if other is lane_record or other["status"] == "merged":
            continue
        if other["status"] == "waiting" and all(lanes[a]["status"] == "merged"
                                                for a in other["after"]):
            start_lane(home, run, other)
            started.append(other["unit"])
            touched.append(other)
        elif (grant := _grant(tdd, other)):
            grants.append(grant)
            touched.append(other)
    everything = all(l["status"] == "merged" for l in lanes.values())
    if everything:
        tdd["no_cheat"], tdd["current"] = True, None
        pending_lines = tdd["lines_since_refactor"] > tdd["refactor_skip_max_lines"]
        tdd["step"] = "refactor" if tdd["directives"] or pending_lines else "final"
    runner.save("tdd-integrated", {
        "unit": unit, "merged_commit": commit, "via": via, "order": list(order),
        "started": started, "grants": grants, "pool": dict(pool),
        "next": tdd["step"] if everything else "lanes"}, lanes=touched)
    return (f"lane {unit} merged at {commit[:12]}"
            + (f"; started {', '.join(started)}" if started else "")
            + (f"; every lane merged, next step {tdd['step']}" if everything else ""))


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


def green_context(run, record=None):
    """What the blind green role sees: the item, never a test, and on a
    retry or a repair only sanitized failure lines."""
    tdd = _lane(run, record).tdd
    error = tdd.get("last_error")
    findings = None
    if tdd["mode"] == "repair" and tdd.get("findings"):
        findings = _failure_lines(_read_report(tdd["findings"].get("report")), tdd)
    return {
        **({"unit": tdd["unit"], "writable": list(tdd["owns"])} if record else {}),
        **({"integration": True, "writable": list(model.pending_merge(run)["owns"])}
           if not record and model.pending_merge(run) else {}),
        "current_item": _current_item(tdd, ("id", "text", "needed")),
        "failure": list(error["tail"]) if error else None,
        "findings": findings,
        "last_error": {"check": error["check"], "message": error["message"],
                       "tail": list(error["tail"])} if error else None,
    }


def refactor_context(run, record=None):
    """What the blind refactor role sees: the production files changed in
    this run (in its lane), their diff stat, and the structural directives."""
    lane = _lane(run, record)
    tdd, wt = lane.tdd, lane.worktree
    base = tdd["fork_commit"] if record else run.get("base_commit")
    files, stat = [], ""
    if base:
        files = [p for p in gitio.changed_paths(wt, base, tdd["last_good_commit"])
                 if not globs.match(p, tdd["test_globs"])]
        stat = gitio.diff_stat(wt, base, tdd["last_good_commit"], files) if files else ""
    error = tdd.get("last_error")
    return {
        **({"unit": tdd["unit"], "writable": list(tdd["owns"])} if record else {}),
        "changed_files": files,
        "diff_stat": stat,
        "directives": list(tdd.get("directives") or []),
        "last_error": {"check": error["check"], "message": error["message"],
                       "tail": list(error["tail"])} if error else None,
    }


def red_context(run, record=None):
    """What the red role sees: the list (a lane's own items), the next
    unchecked item (null: the devil pass, add a test or call no_cheat_left),
    and the raw last error."""
    lane = _lane(run, record)
    tdd = lane.tdd
    todo = next((i for i in tdd["items"] if i["status"] == "todo"), None)
    return {
        **({"unit": lane.unit, "writable": lane.owns + lane.tests} if record else {}),
        "current_item": ({k: todo[k] for k in ("id", "text", "kind", "source", "needed", "criteria")}
                         if todo else None),
        "items": [{k: i[k] for k in ("id", "text", "kind", "status", "criteria")}
                  for i in tdd["items"]],
        "criteria": list(run.get("acceptance_criteria") or []),
        "test_command": list(tdd["test_command"]),
        "test_globs": list(tdd["test_globs"]),
        "red_stub_max_lines": tdd["red_stub_max_lines"],
        "redundant": redundant_items(tdd),
        "files": gitio.tracked_files(lane.worktree)[:FILE_LIST_LIMIT],
        "created_tests": sorted(tdd.get("created_tests") or []),
        "last_error": tdd.get("last_error"),
    }
