"""Limits (Phase 5): budget reporting and calibration from real runs.

Defaults are guesses until measured. This module collects what runs
actually used (wall time, operations, repairs, tdd cycles, attempts)
from the run records, summarizes the distribution over completed runs,
and recommends defaults from the 90th percentile — with floors, so a
handful of toy runs cannot calibrate the factory down to nothing, and a
minimum sample, so one run cannot set the defaults. A task's own
budgets always win over calibrated defaults; calibrated defaults
win over the built-ins.
"""

import glob
import math
import os
import time

from . import store
from .errors import StateError

BUILTIN_DEFAULTS = {"time_limit_s": 3600, "op_limit": 300, "repair_cap": 3,
                    "cycle_limit": 50, "attempt_limit": 3}
FLOORS = {"time_limit_s": 600, "op_limit": 20, "repair_cap": 2,
          "cycle_limit": 5, "attempt_limit": 2}
STEPS = {"time_limit_s": 60, "op_limit": 5, "repair_cap": 1,
         "cycle_limit": 1, "attempt_limit": 1}
MIN_SAMPLE = 3
KEYS = ("time_limit_s", "op_limit", "repair_cap", "cycle_limit", "attempt_limit")
MEASURED = {"time_limit_s": "wall_used_s", "op_limit": "ops_used", "repair_cap": "repairs_used",
            "cycle_limit": "cycles", "attempt_limit": "tdd_attempts"}


def percentile(values, pct):
    """Nearest-rank percentile of a non-empty list of numbers."""
    ordered = sorted(values)
    rank = max(1, math.ceil(pct / 100 * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


def round_up(value, step):
    return int(math.ceil(value / step) * step)


def summarize(values):
    if not values:
        return None
    return {
        "n": len(values),
        "p50": percentile(values, 50),
        "p90": percentile(values, 90),
        "max": max(values),
    }


def limits_path(home):
    return os.path.join(home, "limits.json")


def load_limits(home):
    """Calibrated defaults for this state home, or None. A malformed
    file fails closed — a silently ignored limits file would be a
    lie about what the defaults are."""
    path = limits_path(home)
    if not os.path.exists(path):
        return None
    try:
        data = store.load_json(path)
    except (OSError, ValueError) as exc:
        raise StateError(f"limits file {path} is unreadable: {exc}") from exc
    if not isinstance(data, dict):
        raise StateError(f"limits file {path} is not an object")
    limits = dict(BUILTIN_DEFAULTS)
    for key in KEYS + ("max_lanes",):
        if key in data:
            try:
                value = int(data[key])
            except (TypeError, ValueError) as exc:
                raise StateError(f"limits file {path}: {key!r} is not an integer") from exc
            if value <= 0:
                raise StateError(f"limits file {path}: {key!r} must be > 0")
            limits[key] = value
    return limits


def save_limits(home, recommendation, max_lanes=None):
    data = {key: recommendation["recommended"][key] for key in KEYS}
    if max_lanes is not None:
        data["max_lanes"] = max_lanes["max_lanes"]
        data["lanes_calibrated_from"] = {key: max_lanes[key]
                                         for key in ("attempts", "failures", "rate")}
    data["calibrated_from"] = {
        "runs": recommendation["sample"],
        "at": time.time(),
        "stats": recommendation["stats"],
    }
    store.save_json(limits_path(home), data)
    return data


def _hit_repair_cap(record):
    """A run paused on the repair cap asked a human for help: how many
    repairs it needed is unknown, so it cannot calibrate anything. One a
    human then stopped is the same run."""
    escalations = record.get("escalations") or []
    return (record.get("phase") in ("paused", "stopped") and bool(escalations)
            and escalations[-1].get("kind") == "repair-cap")


def collect(home):
    """Read every run record in the state home and split it into
    what can calibrate defaults and what cannot. Completed runs
    (local-verified with measurements) calibrate; runs that paused on
    the repair cap are censored — their true need is unknown — and
    are reported as such, not folded into the numbers."""
    runs_dir = os.path.join(home, "runs")
    runs, unreadable = [], []
    if os.path.isdir(runs_dir):
        for path in sorted(glob.glob(os.path.join(runs_dir, "*.json"))):
            try:
                record = store.load_json(path)
            except (OSError, ValueError):
                unreadable.append(os.path.basename(path))
                continue
            tdd = record.get("tdd") or {}
            runs.append({
                "task_id": record.get("task_id", "?"),
                "repo": record.get("repo"),
                "phase": record.get("phase"),
                "stop_reason": record.get("stop_reason"),
                "repair_cap_hit": _hit_repair_cap(record),
                "superseded": ".superseded-" in os.path.basename(path),
                "measurements": record.get("measurements"),
                "budgets": record.get("budgets", {}),
                "budget_source": record.get("budget_source"),
                "integrations": tdd.get("integrations", 0),
                "integration_failures": list(tdd.get("integration_failures") or []),
            })
    completed = [r for r in runs
                 if r["phase"] == "local-verified" and r["measurements"]
                 and not r["superseded"]]
    censored = [r for r in runs if r["repair_cap_hit"] and not r["superseded"]]
    return {
        "runs": runs,
        "unreadable": unreadable,
        "integration": integration_by_project(runs),
        "completed": completed,
        "censored": censored,
        "cap_hits": {
            "repair": len(censored),
            "ops": len([r for r in runs
                        if r["budgets"].get("ops_used", 0) >= r["budgets"].get("op_limit", 0)
                        and r["budgets"].get("op_limit")]),
            "time": len([r for r in runs
                         if r["measurements"]
                         and r["measurements"].get("wall_used_s", 0) >= r["budgets"].get("time_limit_s", 0)
                         and r["budgets"].get("time_limit_s")]),
        },
    }


def integration_by_project(runs):
    """Merge attempts and failures of the runs' lanes, per project (the run's
    repo): {repo: {attempts, failures, rate, routes}}. `rate` is failures per
    attempt; `routes` counts failures by the route they took through
    integrate. Runs that never merged a lane are left out."""
    projects = {}
    for run in runs:
        if not run["integrations"]:
            continue
        entry = projects.setdefault(run["repo"], {"attempts": 0, "failures": 0, "routes": {}})
        entry["attempts"] += run["integrations"]
        entry["failures"] += len(run["integration_failures"])
        for failure in run["integration_failures"]:
            entry["routes"][failure["route"]] = entry["routes"].get(failure["route"], 0) + 1
    for entry in projects.values():
        entry["rate"] = entry["failures"] / entry["attempts"]
    return projects


# A project whose merges fail more often gets fewer lanes: at most 20% of merge
# attempts failing keeps the default, at most 50% two lanes, above that one
# (the analyzer's units are then ignored and the run is serial).
LANE_STEPS = ((0.2, 3), (0.5, 2))


def recommend_lanes(data):
    """The calibrated `max_lanes` for this state home from the merge attempts
    its runs made, or None before MIN_SAMPLE of them (one project's runs are
    all a home holds)."""
    attempts = sum(p["attempts"] for p in data["integration"].values())
    failures = sum(p["failures"] for p in data["integration"].values())
    if attempts < MIN_SAMPLE:
        return None
    rate = failures / attempts
    max_lanes = next((lanes for ceiling, lanes in LANE_STEPS if rate <= ceiling), 1)
    return {"max_lanes": max_lanes, "attempts": attempts, "failures": failures, "rate": rate}


def recommend(completed):
    """Recommended defaults from completed runs, or None when the
    sample is too small to say anything. The recommendation is the
    p90 rounded up to a sane step, never below the floor. A key no
    completed run measured keeps its built-in default."""
    if len(completed) < MIN_SAMPLE:
        return None
    stats = {}
    recommended = {}
    for key in KEYS:
        measured_key = MEASURED[key]
        values = [r["measurements"][measured_key] for r in completed
                  if isinstance(r["measurements"].get(measured_key), (int, float))]
        summary = summarize(values)
        stats[key] = summary
        if summary is None:
            recommended[key] = BUILTIN_DEFAULTS[key]
        else:
            recommended[key] = max(FLOORS[key], round_up(summary["p90"], STEPS[key]))
    return {"sample": len(completed), "stats": stats, "recommended": recommended}


def _stat_text(label, summary, fmt="{}"):
    if summary is None:
        return f"{label} n/a"
    return (f"{label} p50 {fmt.format(summary['p50'])} p90 {fmt.format(summary['p90'])} "
            f"max {fmt.format(summary['max'])}")


def report_text(home, data, recommendation):
    lines = [f"budget report — home: {home}"]
    runs = data["runs"]
    verified = len([r for r in runs if r["phase"] == "local-verified"])
    stopped = len([r for r in runs if r["phase"] == "stopped"])
    superseded = len([r for r in runs if r["superseded"]])
    lines.append(
        f"runs: {len(runs)} total "
        f"({verified} verified, {stopped} stopped, {superseded} superseded)"
    )
    if data["unreadable"]:
        lines.append(f"unreadable run records: {', '.join(data['unreadable'])}")
    for entry in runs:
        budgets = entry["budgets"]
        m = entry["measurements"]
        if m:
            wall = m.get("wall_used_s", 0)
            lines.append(
                f"  {entry['task_id']}: {entry['phase']} "
                f"wall {wall:.1f}s/{budgets.get('time_limit_s')}s "
                f"ops {m.get('ops_used')}/{budgets.get('op_limit')} "
                f"repairs {m.get('repairs_used')}/{budgets.get('repair_cap')} "
                f"cycles {m.get('cycles', 0)}/{budgets.get('cycle_limit')}"
                + (" (superseded)" if entry["superseded"] else "")
            )
        else:
            lines.append(
                f"  {entry['task_id']}: {entry['phase']} (no measurements)"
                + (" (superseded)" if entry["superseded"] else "")
            )
    hits = data["cap_hits"]
    lines.append(
        f"cap hits: repairs {hits['repair']}, ops {hits['ops']}, time {hits['time']}"
    )
    if data["censored"]:
        lines.append(
            f"censored from calibration (paused on the repair cap): "
            f"{', '.join(r['task_id'] for r in data['censored'])}"
        )
    for repo, entry in data["integration"].items():
        routes = ", ".join(f"{route} {n}" for route, n in sorted(entry["routes"].items()))
        lines.append(f"integration failures: {repo}: {entry['failures']} of {entry['attempts']} "
                     f"merges ({entry['rate']:.0%})" + (f"; routes: {routes}" if routes else ""))
    lanes = recommend_lanes(data)
    if lanes is not None:
        lines.append(f"recommended max_lanes={lanes['max_lanes']} (merge failure rate "
                     f"{lanes['rate']:.0%} over {lanes['attempts']} merges)")
    if recommendation is None:
        lines.append(
            f"calibration: not enough data "
            f"({len(data['completed'])} completed runs, need at least {MIN_SAMPLE})"
        )
    else:
        rec = recommendation["recommended"]
        stats = recommendation["stats"]
        lines.append(
            f"calibration: from {recommendation['sample']} completed runs — "
            + _stat_text("wall", stats["time_limit_s"], "{:.1f}s") + "; "
            + _stat_text("ops", stats["op_limit"]) + "; "
            + _stat_text("repairs", stats["repair_cap"]) + "; "
            + _stat_text("cycles", stats["cycle_limit"]) + "; "
            + _stat_text("attempts", stats["attempt_limit"])
        )
        lines.append(
            "recommended defaults: " + " ".join(f"{key}={rec[key]}" for key in KEYS)
        )
    return "\n".join(lines) + "\n"
