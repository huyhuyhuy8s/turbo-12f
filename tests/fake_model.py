#!/usr/bin/env python3
"""The model behind tests/fake_omp.py's FAKE_OMP_DELEGATE in the runner
tests: it plays a tiny Python repo (`greet` in app.py, unittest under
tests/) through the TDD roles. fake_omp runs it with the prompt on stdin in
the omp session's cwd, and with `TOOL ERROR: ...` on stdin when the factory
refused the last call, so a role can fix its work in the same session.
It edits files and prints one `{"tool": ..., "args": ...}` object.

Roles, by $T12_ROLE:
  analyzer   one test-list item for criterion 0; in review-fix mode a
             directive that names each review finding
  red        writes tests/test_greet.py; with no current item (the devil
             pass) calls no_cheat_left
  green      writes app.py (it runs where no test file exists)
  refactor   adds a docstring when it has directives, else changes nothing
  reviewer   merge-ready

$FAKE_MODE picks a misbehaviour:
  escalate          the analyzer escalates and nothing else happens
  red-tautology     red's first test already passes; it writes the real one
                    after the refusal
  green-wrong       green's first change does not satisfy the red test
  green-touches-tests  green also writes tests/test_extra.py, and deletes it
                    after the refusal
  review-changes    the first review is blocked with finding F1 (needs
                    $FAKE_STATE, a directory); the next is merge-ready

Every call is appended to $FAKE_LOG: the role, session, cwd, whether it was
a retry after an error and that error, the files it could see, the guard
policy it ran under, and whether the prompt named the test file."""

import json
import os
import re
import sys

APP = 'def greet(name="world"):\n    return f"Hello, {name}!"\n'
APP_REFACTORED = ('def greet(name="world"):\n    """The greeting for `name`."""\n'
                  '    return f"Hello, {name}!"\n')
APP_WRONG = 'def greet(name="world"):\n    return f"Hi, {name}!"\n'
TEST = """import unittest

from app import greet


class TestGreet(unittest.TestCase):
    def test_named(self):
        self.assertEqual(greet("Ana"), "Hello, Ana!")


if __name__ == "__main__":
    unittest.main()
"""
TAUTOLOGY = TEST.replace('self.assertEqual(greet("Ana"), "Hello, Ana!")',
                         "self.assertEqual(greet(), greet())")
MARKER = "--- context (JSON) ---"
ERROR = "TOOL ERROR: "
TEST_FILE = "tests/test_greet.py"

prompt = sys.stdin.read()
role, mode = os.environ["T12_ROLE"], os.environ.get("FAKE_MODE", "")
retry = prompt.startswith(ERROR)
context = {} if retry or MARKER not in prompt else json.loads(prompt.rsplit(MARKER + "\n", 1)[1])


def files():
    found = []
    for root, dirs, names in os.walk("."):
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__")]
        found += [os.path.relpath(os.path.join(root, n), ".") for n in names]
    return sorted(found)


def guard_policy():
    path = os.environ.get("T12_GUARD_POLICY")
    if not path:
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


with open(os.environ["FAKE_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps({
        "role": role, "session": os.environ["T12_SESSION"], "cwd": os.getcwd(),
        "retry": retry, "error": prompt[len(ERROR):] if retry else None,
        "files": files(), "guard": guard_policy(),
        "prompt_names_test": "test_greet" in prompt,
        "mode": context.get("mode"),
    }) + "\n")


def say(tool, **args):
    print(json.dumps({"tool": tool, "args": args}))


def write(path, text):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def finding_ids(node):
    """Ids of the review findings in a context field, whatever shape the
    core gives them (a list of findings, or a report as text)."""
    ids = []
    if isinstance(node, dict):
        if "id" in node and "finding" in node:
            ids.append(str(node["id"]))
        for value in node.values():
            ids += finding_ids(value)
    elif isinstance(node, list):
        for value in node:
            ids += finding_ids(value)
    elif isinstance(node, str):
        try:
            ids += finding_ids(json.loads(node))
        except ValueError:
            ids += re.findall(r'"id":\s*"([^"]+)"', node)
    return ids


def last_assertion_line(text):
    return max(n for n, line in enumerate(text.splitlines(), 1) if "self.assert" in line)


def stay_hidden_from_tests():
    """Green and refactor run where no test file exists."""
    if os.path.exists("tests"):
        sys.exit("fake model: a test path is visible to a blind role")


if mode == "escalate" and role == "analyzer":
    say("escalate", question="Which greeting does the product want?")
elif role == "analyzer":
    criteria = context.get("acceptance_criteria") or context.get("criteria") or ["c0"]
    if context.get("mode") == "review-fix":
        ids = sorted(set(finding_ids(context.get("findings")))) or ["F1"]
        say("test_list_ready", items=[], gaps=[],
            directives=[f"{i}: document greet() with a docstring" for i in ids])
    else:
        say("test_list_ready", gaps=[], directives=[], items=[{
            "text": "greet(name) greets the name", "kind": "behavior",
            "source": "criterion 0", "needed": "greet('Ana') returns 'Hello, Ana!'",
            "criteria": list(range(len(criteria)))}])
elif role == "red":
    item = context.get("current_item")
    if not item and not retry:
        say("no_cheat_left", reason="greet() answers for the default and for any name")
    else:
        if mode == "red-tautology" and not retry:
            body = TAUTOLOGY
        else:
            body = TEST
        write(TEST_FILE, body)
        say("red_test_ready", item_id=(item or {}).get("id", "T1"), test_file=TEST_FILE,
            test_name="test_named", last_assertion_line=last_assertion_line(body),
            defeats="a greet() that ignores its argument", add_items=[])
elif role == "green":
    if not retry:
        stay_hidden_from_tests()
    if mode == "green-touches-tests":
        if retry:
            os.remove("tests/test_extra.py")
            os.rmdir("tests")
            write("app.py", APP)
        else:
            write("tests/test_extra.py", "import unittest\n")
            write("app.py", APP)
    else:
        write("app.py", APP_WRONG if mode == "green-wrong" and not retry else APP)
    say("green_ready", summary="greet() takes a name")
elif role == "refactor":
    if not retry:
        stay_hidden_from_tests()
    if context.get("directives"):
        write("app.py", APP_REFACTORED)
        say("refactor_done", summary="documented greet()", changed=True)
    else:
        say("refactor_done", summary="nothing to tidy", changed=False)
else:
    marker = os.path.join(os.environ.get("FAKE_STATE", ""), "reviewed-once")
    if mode == "review-changes" and not os.path.exists(marker):
        write(marker, "")
        say("review", verdict="blocked", verification="read app.py",
            findings=[{"id": "F1", "class": "structure", "severity": "minor", "where": "app.py",
                       "finding": "greet() has no docstring", "owner": "refactor"}])
    else:
        say("review", verdict="merge-ready", findings=[],
            verification="read the diff and the gate results")
