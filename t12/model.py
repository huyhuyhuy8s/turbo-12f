"""The run record: one task, one record, execution state and business
state unified (Factor 5). The state machine is the only control flow
(Factor 8); each CLI invocation is a stateless reducer over the record
on disk (Factor 12): load state, apply one event, persist atomically.
"""

import contextlib
import fcntl
import json
import os
import re
import time

from . import limits as limits_mod
from . import profile as profile_mod
from . import store
from .errors import BudgetError, StateError

VERSION = 2
TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
PROVENANCE = "observed-helper-execution"

PHASES = (
    "unadmitted",
    "ready",
    "tdd",
    "gates",
    "review",
    "local-verified",
    "paused",
    "stopped",
)

# Allowed transitions. "gates -> tdd" is a gate failure: the run goes
# back to green with the failing output as findings. "review -> tdd" is
# changes-requested: back to the analyzer with the review findings. Both
# are repairs and count against the repair cap; a candidate is only
# pinned again by the final step of the tdd loop (tdd -> gates).
TRANSITIONS = {
    "unadmitted": {"ready"},
    "ready": {"tdd", "paused", "stopped"},
    "tdd": {"gates", "paused", "stopped"},
    "gates": {"tdd", "review", "paused", "stopped"},
    "review": {"tdd", "local-verified", "paused", "stopped"},
    "local-verified": set(),
    "paused": {"ready", "tdd", "gates", "review", "stopped"},
    "stopped": set(),
}

TDD_STEPS = ("analyzer", "red", "green", "refactor", "final", "done")
TDD_MODES = ("initial", "repair", "review-fix")
ATTEMPT_STEPS = ("analyzer", "red", "green", "refactor")
# Where a repair re-enters the loop, by what sent the run back.
REPAIR_ENTRY = {"gate": ("green", "repair"), "review": ("analyzer", "review-fix")}


def run_path(home, task_id):
    return os.path.join(home, "runs", f"{task_id}.json")


def events_path(home, task_id):
    return os.path.join(home, "runs", f"{task_id}.events.jsonl")


# Long enough to outlast `board` probing the lock, short enough that a
# command queued behind a running gate still fails fast.
LOCK_WAIT_S = 2.0


def lock_path(home, task_id):
    return os.path.join(home, "runs", f"{task_id}.lock")


def is_busy(home, task_id):
    """True while another command holds the run's lock."""
    try:
        fd = os.open(lock_path(home, task_id), os.O_RDWR)
    except FileNotFoundError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return False
    except BlockingIOError:
        return True
    finally:
        os.close(fd)


@contextlib.contextmanager
def run_lock(home, task_id):
    """Hold the run's lock for one command. The kernel releases it when
    the process exits, so a crashed command never leaves it held."""
    if not isinstance(task_id, str) or not TASK_ID_RE.match(task_id):
        raise StateError(f"task id must match {TASK_ID_RE.pattern}: {task_id!r}")
    path = lock_path(home, task_id)
    if not os.path.isdir(os.path.dirname(path)):
        raise StateError(f"no turbo-12f state home at {home}; run `init` first")
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    deadline = time.monotonic() + LOCK_WAIT_S
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise StateError(f"run {task_id!r} is busy: another t12 command holds "
                                     f"{path}; retry when it finishes") from None
                time.sleep(0.05)
        yield
    finally:
        os.close(fd)


def verify_checkout_dir(home, task_id):
    return os.path.join(home, "verify", task_id)


def evidence_dir(home, task_id):
    return os.path.join(home, "evidence", task_id)


def bound_sessions(run):
    """All session ids bound so far, as {session: role}."""
    bound = {}
    for role, record in (run.get("roles") or {}).items():
        bound[record["session"]] = role
    for review in run.get("reviews") or []:
        bound[review["session"]] = review["role"]
    for record in run.get("bindings") or []:
        bound[record["session"]] = record["role"]
    return bound


def role_kind(run, role):
    """The kind of a role in the run's frozen role map."""
    roles = run.get("role_map")
    if roles is None:
        raise StateError("this run was prepared before named roles; "
                         "stop it and prepare it again with --supersede")
    if role not in roles:
        raise StateError(f"role {role!r} is not in this run; roles are {sorted(roles)}")
    return roles[role]["kind"]


def check_session(run, role, session):
    """One session per role per run. A session already bound to a
    different role cannot take this one — that would collapse the
    separation the roles exist to enforce: the same worker cannot write
    the tests and the implementation, or review its own code."""
    role_kind(run, role)
    if not session or not str(session).strip():
        raise StateError(f"--session is required for the {role} role")
    bound = bound_sessions(run)
    other = bound.get(session)
    if other is not None and other != role:
        raise StateError(
            f"session {session!r} is already bound to the {other} role; "
            f"a separate worker session is required for {role}"
        )


def bind_session(run, role, session):
    check_session(run, role, session)
    run.setdefault("roles", {})[role] = {"session": session, "at": time.time()}
    # roles keeps the latest session per role; the history keeps every
    # one, so a re-run under a new session does not free the old one to
    # take another role.
    run.setdefault("bindings", []).append({"role": role, "session": session})


def latest_review(run, role, commit=None):
    """The latest non-voided review for a role, optionally on one commit."""
    for review in reversed(run.get("reviews") or []):
        if review["role"] != role or review["voided"]:
            continue
        if commit is not None and review["commit"] != commit:
            continue
        return review
    return None


def changes_requested(run):
    """The changes-requested review that stands on the current
    candidate (the latest non-voided review of a required role), or None."""
    candidate = (run.get("candidate") or {}).get("commit")
    if not candidate:
        return None
    for role in run.get("required_reviews") or []:
        review = latest_review(run, role, commit=candidate)
        if review is not None and review["verdict"] == "changes-requested":
            return review
    return None


def check_reviews(run):
    """Check requires an approving review for every required role, on
    the current candidate. A changes-requested review newer than the
    latest approval blocks; a missing review blocks."""
    candidate = (run["candidate"] or {}).get("commit")
    if not candidate:
        raise StateError("check requires a pinned candidate")
    for role in run.get("required_reviews") or []:
        review = latest_review(run, role, commit=candidate)
        if review is None:
            raise StateError(
                f"check requires an approving {role} review on {candidate[:12]}; none recorded"
            )
        if review["verdict"] != "approve":
            raise StateError(
                f"check blocked: the latest {role} review on {candidate[:12]} "
                f"is {review['verdict']!r}"
            )


def next_step(run):
    """Who acts next on this run, and on what, as one short line. The
    runner routes on it: `tdd-step`, `gate` and `check` are scripted
    steps, `<role>: <verb>` is a worker, `human: ...` waits."""
    phase = run["phase"]
    if phase == "ready":
        return "analyzer: analyze"
    if phase == "tdd":
        step = (run.get("tdd") or {}).get("step", "analyzer")
        if step == "analyzer":
            return "analyzer: analyze"
        if step in ("red", "green", "refactor"):
            return f"{step}: {step}"
        return "tdd-step"
    if phase == "gates":
        return "gate"
    if phase == "review":
        commit = run["candidate"]["commit"]
        for role in run.get("required_reviews") or []:
            review = latest_review(run, role, commit=commit)
            if review is None or review["verdict"] != "approve":
                return f"{role}: review"
        return "check"
    if phase == "local-verified":
        return "human: merge"
    if phase == "paused":
        pending = open_escalation(run)
        if pending:
            return f"human: answer {pending['question']!r}"
        return f"human: resume (was {run['prior_phase']})"
    if phase == "stopped":
        return f"stopped: {run['stop_reason']}"
    return "prepare"


def void_reviews(run, reason):
    """A newly pinned candidate voids reviews bound to the old one. They stay
    in the record (audit trail) and the next review for the role
    starts from the voided one — review remembers."""
    for review in run.get("reviews") or []:
        if not review["voided"]:
            review["voided"] = True
            review["voided_reason"] = reason


def record_measurements(run):
    """Snapshot what a run actually used, at a terminal transition.
    Calibration reads these; a run without measurements cannot
    calibrate anything and is reported as such."""
    budgets = run["budgets"]
    tdd = run.get("tdd") or {}
    history = tdd.get("history") or []
    lint_runs = sum(1 for h in history for key in ("green_lint", "refactor_lint") if h.get(key))
    lint_runs += 1 if (tdd.get("final") or {}).get("lint") else 0
    run["measurements"] = {
        "wall_used_s": round(time.time() - budgets["started_at"], 1),
        "ops_used": budgets["ops_used"],
        "repairs_used": budgets["repairs_used"],
        "cycles": tdd.get("cycle", 0),
        "tdd_attempts": tdd.get("attempts_peak", 0),
        "lint_runs": lint_runs,
        "gates": len(run["gates"]),
        "finished_at": time.time(),
    }
    return run["measurements"]


def paths_overlap(a, b):
    return a == b or a.startswith(b + os.sep) or b.startswith(a + os.sep)


def under(path, roots):
    return any(path == r or path.startswith(r + os.sep) for r in roots)


def outside_touches(run, paths):
    """The paths a run may not change under its declared touches."""
    touches = run.get("touches")
    if touches is None:
        return []
    return [p for p in paths if not under(p, touches)]


def _is_worktree_path(path):
    if not isinstance(path, str) or not path or os.path.isabs(path):
        return False
    first = os.path.normpath(path).split(os.sep)[0]
    return first not in ("..", ".", ".git")


def open_escalation(run):
    escalations = run.get("escalations") or []
    if escalations and escalations[-1]["answer"] is None:
        return escalations[-1]
    return None


def _setting(key, default, *sources):
    for source in sources:
        if source and key in source:
            return source[key]
    return default


def new_tdd(profile, task, base_commit=None):
    """The tdd record at prepare: the loop's position, the test list, the
    settings frozen from the profile (a task value wins where it has one,
    since `profile.apply` has already merged the two), and what to roll back
    to. `last_good_commit` is the base commit until the first accepted step."""
    test_command = _setting("test_command", None, task, profile)
    if not test_command:
        raise StateError("test_command is required: set it in .t12/project.json or the task file")
    return {
        "step": "analyzer",
        "mode": "initial",
        "cycle": 0,
        "attempts": {step: 0 for step in ATTEMPT_STEPS},
        "attempts_peak": 0,
        "items": [],
        "gaps": [],
        "directives": [],
        "current": None,
        "test_command": list(test_command),
        "test_globs": list(_setting("test_globs", ["tests/**"], task, profile)),
        "lint_command": _setting("lint_command", None, task, profile),
        "assertion_pattern": _setting("assertion_pattern", "AssertionError", task, profile),
        "failure_count_pattern": _setting("failure_count_pattern", None, task, profile),
        "red_stub_max_lines": _setting("red_stub_max_lines", 10, task, profile),
        "refactor_skip_max_lines": _setting("refactor_skip_max_lines", 5, task, profile),
        "lines_since_refactor": 0,
        "locked": {},
        "last_good_commit": base_commit,
        "red_commit": None,
        "green_commit": None,
        "red": None,
        "history": [],
        "final": None,
        "last_error": None,
        "findings": None,
    }


def new_item(tdd, spec):
    """Append a test-list item with the next id ("T1", "T2", ...). Ids are
    never reused: items are never removed."""
    item = {
        "id": f"T{len(tdd['items']) + 1}",
        "text": spec["text"],
        "kind": spec["kind"],
        "source": spec["source"],
        "needed": spec["needed"],
        "criteria": list(spec["criteria"]),
        "status": "todo",
        "test_file": None,
        "test_name": None,
        "defeats": None,
    }
    tdd["items"].append(item)
    return item


def bump_attempt(tdd, step):
    """One failed try of a worker step since its last accept. Returns the
    count; the caller compares it with budgets.attempt_limit."""
    tdd["attempts"][step] += 1
    tdd["attempts_peak"] = max(tdd.get("attempts_peak", 0), tdd["attempts"][step])
    return tdd["attempts"][step]


def repair_or_pause(run, source, findings_ref):
    """A gate failure (`source` "gate") or a changes-requested review
    ("review") sends the run back into the tdd loop: green in repair mode,
    or the analyzer in review-fix mode, with `findings_ref` as its findings.
    That is one repair. Past the repair cap the run pauses with an
    escalation of kind "repair-cap" instead of stopping, and resumes into the
    same tdd step. Returns the run's new phase, "tdd" or "paused"."""
    if source not in REPAIR_ENTRY:
        raise StateError(f"repair source must be one of {sorted(REPAIR_ENTRY)}, got {source!r}")
    step, mode = REPAIR_ENTRY[source]
    budgets = run["budgets"]
    budgets["repairs_used"] += 1
    tdd = run["tdd"]
    tdd["step"] = step
    tdd["mode"] = mode
    tdd["findings"] = {"source": source, "report": findings_ref}
    tdd["attempts"] = {name: 0 for name in ATTEMPT_STEPS}
    if budgets["repairs_used"] <= budgets["repair_cap"]:
        transition(run, "tdd")
        return "tdd"
    run["prior_phase"] = "tdd"
    run.setdefault("escalations", []).append({
        "question": (f"repair cap exceeded: {budgets['repairs_used']} repairs used, "
                     f"{budgets['repair_cap']} allowed; the {source} findings are in "
                     f"{findings_ref}. Answer with guidance to continue."),
        "kind": "repair-cap",
        "asked_at": time.time(),
        "answer": None,
        "answered_at": None,
    })
    transition(run, "paused")
    return "paused"


def tdd_summary(run):
    """The tdd loop in a few numbers, for review context and the manifest."""
    tdd = run.get("tdd") or {}
    items = tdd.get("items") or []
    final = tdd.get("final") or {}
    return {
        "step": tdd.get("step"),
        "mode": tdd.get("mode"),
        "cycles": tdd.get("cycle", 0),
        "items_done": sum(1 for i in items if i["status"] == "done"),
        "items_total": len(items),
        "final": final.get("tests"),
        "final_commit": final.get("commit"),
        "final_lint": final.get("lint"),
    }


SOURCE_URL_RE = re.compile(r"https?://\S+")


def _source(task):
    """Where the task came from, as an intake adapter wrote it. Kept to
    one line per field so a delivery adapter can quote it in a change
    request without escaping."""
    source = task.get("source")
    if source is None:
        return None
    url = source.get("url") if isinstance(source, dict) else None
    if (not isinstance(source, dict) or set(source) - {"adapter", "ref", "url"}
            or not all(isinstance(source.get(k), str) and re.fullmatch(r"\S+", source[k])
                       for k in ("adapter", "ref"))
            or (url is not None and not (isinstance(url, str) and SOURCE_URL_RE.fullmatch(url)))):
        raise StateError(
            'source must be {"adapter": str, "ref": str, "url": http(s) URL (optional)}, '
            f"each without whitespace: {source!r}")
    return {"adapter": source["adapter"], "ref": source["ref"], "url": url}


def new_run(task, repo, home, base_commit, roles, supersedes=None, defaults=None,
            defaults_source="builtin", sandbox_adapter=None, sandbox_image=None):
    now = time.time()
    defaults = defaults or limits_mod.BUILTIN_DEFAULTS
    budget_source = defaults_source
    limits = {}
    for key in limits_mod.KEYS:
        if key in task:
            limits[key] = int(task[key])
            budget_source = "task"
        else:
            limits[key] = int(defaults.get(key, limits_mod.BUILTIN_DEFAULTS[key]))
    gates = []
    for g in task.get("gates", []):
        if not isinstance(g.get("command"), list) or not g["command"]:
            raise StateError(f"gate {g.get('name')!r} must have a non-empty argv command")
        gates.append(
            {"name": g["name"], "command": list(g["command"]), "status": "pending",
             "observation": None, "commit": None}
        )
    if not gates:
        raise StateError("task must declare at least one gate")
    if not task.get("acceptance_criteria"):
        raise StateError("task must declare at least one acceptance criterion")
    required_reviews = task.get("required_reviews", ["reviewer"])
    reviewers = profile_mod.review_roles(roles)
    if not isinstance(required_reviews, list) or any(
            r not in reviewers for r in required_reviews):
        raise StateError(
            f"required_reviews must be a list of review roles {reviewers}: {required_reviews!r}"
        )
    verify_deps = task.get("verify_deps", [])
    if not isinstance(verify_deps, list) or not all(_is_worktree_path(d) for d in verify_deps):
        raise StateError(
            f"verify_deps must be a list of relative paths inside the worktree: {verify_deps!r}"
        )
    normalized = [os.path.normpath(d) for d in verify_deps]
    for i, a in enumerate(normalized):
        for b in normalized[i + 1:]:
            if paths_overlap(a, b):
                raise StateError(f"verify_deps entries overlap: {a!r} and {b!r}")
    touches = task.get("touches")
    if touches is not None:
        if not isinstance(touches, list) or not touches or not all(
                _is_worktree_path(t) for t in touches):
            raise StateError(
                f"touches must be a non-empty list of relative paths inside the worktree: {touches!r}"
            )
        touches = sorted({os.path.normpath(t) for t in touches})
    depends_on = task.get("depends_on", [])
    if not isinstance(depends_on, list) or not all(
            isinstance(d, str) and TASK_ID_RE.match(d) for d in depends_on):
        raise StateError(f"depends_on must be a list of task ids: {depends_on!r}")
    source = _source(task)
    return {
        "version": VERSION,
        "task_id": task["id"],
        "title": task.get("title", task["id"]),
        "acceptance_criteria": list(task["acceptance_criteria"]),
        "repo": repo,
        "base": task.get("base", "main"),
        "base_commit": base_commit,
        "touches": touches,
        "depends_on": list(depends_on),
        "source": source,
        "branch": task.get("branch") or f"task/{task['id']}",
        "worktree": os.path.join(home, "worktrees", task["id"]),
        "phase": "unadmitted",
        "prior_phase": None,
        "supersedes": supersedes,
        "created_at": now,
        "config_digest": store.sha256_bytes(store.canonical_json(task)),
        "budgets": {
            "time_limit_s": limits["time_limit_s"],
            "op_limit": limits["op_limit"],
            "repair_cap": limits["repair_cap"],
            "cycle_limit": limits["cycle_limit"],
            "attempt_limit": limits["attempt_limit"],
            "started_at": now,
            "ops_used": 0,
            "repairs_used": 0,
        },
        "budget_source": budget_source,
        "measurements": None,
        "gates": gates,
        "tdd": None,
        "candidate": None,
        "stop_reason": None,
        "require_probes": bool(task.get("require_probes", False)),
        "probes": [],
        "sandbox_adapter": sandbox_adapter,
        "sandbox_image": sandbox_image,
        "role_map": roles,
        "roles": {},
        "reviews": [],
        "required_reviews": list(required_reviews),
        "verify_deps": normalized,
        "escalations": [],
        "deliveries": [],
        "github_repo": task.get("github_repo"),
        "gitlab_project": task.get("gitlab_project"),
        "bitbucket_repo": task.get("bitbucket_repo"),
        "asana_task_gid": task.get("asana_task_gid"),
    }


def load_run(home, task_id):
    path = run_path(home, task_id)
    if not os.path.exists(path):
        raise StateError(f"no run for task {task_id!r} in {home}")
    run = store.load_json(path)
    if run.get("version") != VERSION:
        raise StateError(f"unsupported run version: {run.get('version')}")
    return run


def archive_run(home, run):
    """Supersession, not reset: move the old run record and its event
    log aside under a timestamped name. Nothing is overwritten or
    deleted; the archived record keeps every SHA and evidence ref."""
    ts = int(time.time())
    base = f"{run['task_id']}.superseded-{ts}"
    os.replace(run_path(home, run["task_id"]),
               os.path.join(home, "runs", f"{base}.json"))
    ev_src = events_path(home, run["task_id"])
    if os.path.exists(ev_src):
        os.replace(ev_src, os.path.join(home, "runs", f"{base}.events.jsonl"))
    return base


def save_run(home, run):
    store.save_json(run_path(home, run["task_id"]), run)


def genesis_prev(task_id):
    return store.sha256_bytes(f"t12-genesis:{task_id}".encode())


def read_events(home, task_id):
    """Load the event log. Raises StateError on a missing or malformed
    log — a log that cannot be read cannot be trusted."""
    path = events_path(home, task_id)
    if not os.path.exists(path):
        raise StateError(f"no event log for task {task_id!r}")
    events = []
    with open(path, encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                raise StateError(f"event log line {lineno} is not valid JSON: {exc}") from exc
            if not isinstance(event, dict):
                raise StateError(f"event log line {lineno} is not an object")
            events.append(event)
    return events


def append_event(home, run, event_type, detail=None):
    """Append one event to the hash-chained log and return it.

    Each event links to the previous event's hash, carries its
    sequence number, and records a digest of the run record as it
    stands after this command. Editing, deleting, or reordering an
    event breaks the chain; editing the run record after the last
    event breaks the state digest. The chain is the audit trail;
    the run record is the current state."""
    events = []
    path = events_path(home, run["task_id"])
    if os.path.exists(path):
        events = read_events(home, run["task_id"])
    prev = events[-1]["hash"] if events else genesis_prev(run["task_id"])
    event = {
        "seq": len(events) + 1,
        "ts": time.time(),
        "task_id": run["task_id"],
        "phase": run["phase"],
        "type": event_type,
        "detail": detail or {},
        "prev": prev,
        "state_sha": store.sha256_bytes(store.canonical_json(run)),
    }
    event["hash"] = store.sha256_bytes(store.canonical_json(event))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, sort_keys=True) + "\n")
    return event


def verify_chain(home, run):
    """Check the event log against the run record. Returns a list of
    problems; empty means the log is intact and the run record is
    exactly what the last event recorded."""
    problems = []
    try:
        events = read_events(home, run["task_id"])
    except StateError as exc:
        return [str(exc)]
    if not events:
        return ["event log is empty"]
    prev = genesis_prev(run["task_id"])
    for index, event in enumerate(events):
        if event.get("seq") != index + 1:
            problems.append(f"event {index + 1}: seq is {event.get('seq')}, expected {index + 1}")
        if event.get("task_id") != run["task_id"]:
            problems.append(f"event {index + 1}: task_id mismatch")
        if event.get("prev") != prev:
            problems.append(f"event {index + 1}: prev link broken")
        recorded = event.get("hash")
        content = {k: v for k, v in event.items() if k != "hash"}
        if recorded != store.sha256_bytes(store.canonical_json(content)):
            problems.append(f"event {index + 1}: hash mismatch (edited?)")
        prev = recorded
    last = events[-1]
    if last.get("state_sha") != store.sha256_bytes(store.canonical_json(run)):
        problems.append("run record changed since the last event (state digest mismatch)")
    return problems


def transition(run, to):
    allowed = TRANSITIONS.get(run["phase"], set())
    if to == run["phase"]:
        return run
    if to not in allowed:
        raise StateError(
            f"cannot move {run['task_id']} from {run['phase']} to {to}; "
            f"allowed: {sorted(allowed) or ['none']}"
        )
    run["phase"] = to
    return run


def check_budgets(run):
    """Fail closed when a budget is exhausted. Call before any step."""
    budgets = run["budgets"]
    wall = time.time() - budgets["started_at"]
    if wall > budgets["time_limit_s"]:
        raise BudgetError(
            f"time budget exhausted: {wall:.0f}s used of {budgets['time_limit_s']}s"
        )
    if budgets["ops_used"] >= budgets["op_limit"]:
        raise BudgetError(
            f"operation budget exhausted: {budgets['ops_used']} of {budgets['op_limit']}"
        )
    if budgets["repairs_used"] > budgets["repair_cap"]:
        raise BudgetError(
            f"repair cap exceeded: {budgets['repairs_used']} of {budgets['repair_cap']}"
        )
    return {"wall_used_s": round(wall, 1)}


def time_left_s(run):
    return max(0, run["budgets"]["time_limit_s"] - (time.time() - run["budgets"]["started_at"]))


def bump_op(run):
    run["budgets"]["ops_used"] += 1
