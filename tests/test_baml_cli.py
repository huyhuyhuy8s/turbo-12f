"""`main(["submit", ...])` reads the reply, calls `t12 act`, appends the evidence line."""

import contextlib
import importlib.util
import io
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_SPEC = importlib.util.spec_from_file_location(
    "baml_run_cli", os.path.join(ROOT, "experiments", "baml", "run.py"))
run = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(run)


class SubmitAccepted(unittest.TestCase):
    def test_valid_review_call_returns_0_and_prints_the_act_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            reply = os.path.join(tmp, "reply.txt")
            with open(reply, "w") as fh:
                fh.write('{"tool": "escalate", "question": "q"}')
            home = os.path.join(tmp, "home")
            os.makedirs(home)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = run.main(
                    ["submit", "--kind", "review", "--reply-file", reply, "--home", home,
                     "--task-id", "T", "--role", "R", "--session", "S",
                     "--evidence", os.path.join(tmp, "evidence.jsonl")],
                    parse=lambda text: SimpleNamespace(tool="escalate", question="q"),
                    act=lambda call: (True, "ok"))
            self.assertEqual((0, "ok"), (code, out.getvalue().strip()))

    def test_valid_review_call_hands_the_parsed_call_to_act_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            reply = os.path.join(tmp, "reply.txt")
            with open(reply, "w") as fh:
                fh.write('{"tool": "escalate", "question": "q"}')
            home = os.path.join(tmp, "home")
            os.makedirs(home)
            calls = []

            def act(call):
                calls.append(call)
                return True, "ok"

            with contextlib.redirect_stdout(io.StringIO()):
                run.main(
                    ["submit", "--kind", "review", "--reply-file", reply, "--home", home,
                     "--task-id", "T", "--role", "R", "--session", "S",
                     "--evidence", os.path.join(tmp, "evidence.jsonl")],
                    parse=lambda text: SimpleNamespace(tool="escalate", question="q"),
                    act=act)
            self.assertEqual([{"tool": "escalate", "args": {"question": "q"}}], calls)

    def test_valid_review_call_appends_one_evidence_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            reply = os.path.join(tmp, "reply.txt")
            with open(reply, "w") as fh:
                fh.write('{"tool": "escalate", "question": "q"}')
            home = os.path.join(tmp, "home")
            os.makedirs(home)
            evidence = os.path.join(tmp, "evidence.jsonl")
            with contextlib.redirect_stdout(io.StringIO()):
                run.main(
                    ["submit", "--kind", "review", "--reply-file", reply, "--home", home,
                     "--task-id", "T", "--role", "R", "--session", "S",
                     "--evidence", evidence],
                    parse=lambda text: SimpleNamespace(tool="escalate", question="q"),
                    act=lambda call: (True, "ok"))
            lines = []
            if os.path.exists(evidence):
                with open(evidence) as fh:
                    lines = fh.read().splitlines()
            self.assertEqual(
                ['{"arm": "B", "kind": "review", "ok": true, "refused_by": null, "error": null}'],
                lines)


class SubmitBamlFailure(unittest.TestCase):
    def test_parse_failure_returns_1_prints_the_error_and_skips_act(self):
        with tempfile.TemporaryDirectory() as tmp:
            reply = os.path.join(tmp, "reply.txt")
            with open(reply, "w") as fh:
                fh.write("not a call")
            home = os.path.join(tmp, "home")
            os.makedirs(home)
            calls = []

            def parse(text):
                raise ValueError("boom")

            def act(call):
                calls.append(call)
                return True, "ok"

            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = run.main(
                    ["submit", "--kind", "review", "--reply-file", reply, "--home", home,
                     "--task-id", "T", "--role", "R", "--session", "S",
                     "--evidence", os.path.join(tmp, "evidence.jsonl")],
                    parse=parse, act=act)
            self.assertEqual((1, "boom", []), (code, out.getvalue().strip(), calls))


if __name__ == "__main__":
    unittest.main()
