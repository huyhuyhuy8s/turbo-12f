"""The gate ladder. Gates run in task-file order; a later gate cannot
run until every prior gate passed on the same candidate. A failed gate
counts as a repair: the run goes back into the tdd loop, and past the
repair cap it pauses for a human. A script runs them, `gate` takes the
next pending one by default.
"""

from .errors import StateError


def next_gate(run):
    for gate in run["gates"]:
        if gate["status"] not in ("passed", "skipped"):
            return gate
    return None


def all_passed(run):
    return all(g["status"] in ("passed", "skipped") for g in run["gates"])


def find_gate(run, name):
    for gate in run["gates"]:
        if gate["name"] == name:
            return gate
    raise StateError(f"no gate named {name!r}; gates are {[g['name'] for g in run['gates']]}")


def check_order(run, gate):
    for prior in run["gates"]:
        if prior is gate:
            break
        if prior["status"] not in ("passed", "skipped"):
            raise StateError(
                f"gate {gate['name']!r} cannot run before gate {prior['name']!r} passes"
            )


def reset_for_new_candidate(run):
    """A newly pinned candidate invalidates gate observations bound to the old one."""
    for gate in run["gates"]:
        gate["status"] = "pending"
        gate["observation"] = None
        gate["commit"] = None
