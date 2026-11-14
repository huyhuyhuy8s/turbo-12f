"""Evidence: write an artifact with its hash, verify it later by
re-reading and re-hashing. Certification re-verifies every ref;
a missing file, a changed byte, a symlink, or a path outside the
state home fails closed.
"""

import os

from . import model, store
from .errors import EvidenceError


def write_evidence(home, task_id, name, data: bytes):
    directory = model.evidence_dir(home, task_id)
    store.mkdir_private(directory)
    path = os.path.join(directory, name)
    store.atomic_write(path, data)
    return {
        "name": name,
        "path": path,
        "sha256": store.sha256_bytes(data),
        "bytes": len(data),
        "provenance": model.PROVENANCE,
    }


def write_observation(home, task_id, name, observation):
    """Store a command observation (argv, exit, duration, output)."""
    text = (
        f"$ {' '.join(observation['argv'])}\n"
        f"exit={observation['exit_code']} timed_out={observation['timed_out']} "
        f"duration_s={observation['duration_s']} sandbox={observation.get('sandbox', 'none')} "
        f"deps={','.join(observation.get('deps', []))}"
        + (f" toolchain={','.join(observation['toolchain_dirs'])}"
           if observation.get("toolchain_dirs") else "") + "\n"
        f"--- stdout ---\n{observation['stdout']}"
        f"--- stderr ---\n{observation['stderr']}"
    )
    ref = write_evidence(home, task_id, name, text.encode())
    ref["observation"] = {
        "argv": observation["argv"],
        "exit_code": observation["exit_code"],
        "timed_out": observation["timed_out"],
        "duration_s": observation["duration_s"],
        "sandbox": observation.get("sandbox", "none"),
        "deps": list(observation.get("deps", [])),
        **({"toolchain_dirs": list(observation["toolchain_dirs"])}
           if observation.get("toolchain_dirs") else {}),
    }
    return ref


def verify_ref(home, task_id, ref):
    if not ref or ref.get("provenance") != model.PROVENANCE:
        raise EvidenceError(f"evidence ref missing provenance: {ref and ref.get('name')}")
    path = store.ensure_inside(ref["path"], home)
    if not os.path.exists(path):
        raise EvidenceError(f"evidence missing: {ref['name']} ({path})")
    actual = store.sha256_file(path)
    if actual != ref["sha256"]:
        raise EvidenceError(
            f"evidence changed: {ref['name']} (recorded {ref['sha256'][:12]}, "
            f"on disk {actual[:12]})"
        )
    return True


def _obs(ref):
    return (ref or {}).get("observation", {})


def _expect_exit(home, task_id, ref, label, passing):
    """The ref hashes, and its recorded observation tells the story the
    step claims: exit 0 for a check that passed, non-zero (not a timeout)
    for a red."""
    verify_ref(home, task_id, ref)
    obs = _obs(ref)
    if obs.get("timed_out"):
        raise EvidenceError(f"{label} evidence is a timeout, not a result")
    if (obs.get("exit_code") == 0) != passing:
        raise EvidenceError(
            f"{label} evidence does not show a {'passing' if passing else 'failing'} run")


def verify_tdd(home, run):
    """Every artifact of the tdd loop re-hashed and re-read: each red
    failed, each green and refactor check passed, the final run on the
    candidate passed. Hashes alone do not prove a test passed, so the
    recorded exit codes must agree with the statuses."""
    task_id = run["task_id"]
    tdd = run.get("tdd")
    if not tdd or not tdd.get("final"):
        raise EvidenceError("check requires the tdd final run")
    final = tdd["final"]
    if final["commit"] != (run["candidate"] or {}).get("commit"):
        raise EvidenceError("the tdd final run was not made on the candidate")
    _expect_exit(home, task_id, final["tests"], "final test", True)
    if tdd.get("lint_command"):
        if not final.get("lint"):
            raise EvidenceError("lint is configured but the final run has no lint evidence")
        _expect_exit(home, task_id, final["lint"], "final lint", True)
    for entry in tdd.get("history") or []:
        where = f"cycle {entry.get('cycle')}"
        if entry.get("kind") == "cycle":
            _expect_exit(home, task_id, entry.get("red"), f"{where} red", False)
        for key in ("green_tests", "green_lint", "refactor_tests", "refactor_lint"):
            if entry.get(key):
                _expect_exit(home, task_id, entry[key], f"{where} {key}", True)
        if entry.get("kind") == "cycle" and tdd.get("lint_command") and not entry.get("green_lint"):
            raise EvidenceError(f"{where} has no green lint evidence")
    return True


def verify_check(home, run):
    """Re-validate the whole chain before certifying. Raises on any gap."""
    task_id = run["task_id"]
    if run["phase"] != "review":
        raise EvidenceError(f"check requires phase 'review', run is {run['phase']!r}")
    if not run["candidate"]:
        raise EvidenceError("check requires a pinned candidate")
    verify_tdd(home, run)
    for gate in run["gates"]:
        if gate["status"] != "passed" or not gate["observation"]:
            raise EvidenceError(f"gate {gate['name']!r} has not passed on this candidate")
        if gate.get("commit") != run["candidate"]["commit"]:
            raise EvidenceError(f"gate {gate['name']!r} was not run on the candidate")
        verify_ref(home, task_id, gate["observation"])
        gate_obs = _obs(gate["observation"])
        if gate_obs.get("exit_code") != 0 or gate_obs.get("timed_out"):
            raise EvidenceError(
                f"gate {gate['name']!r} is marked passed but its observation failed or timed out"
            )
    return True
