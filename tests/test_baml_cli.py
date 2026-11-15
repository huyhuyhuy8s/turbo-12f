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


if __name__ == "__main__":
    unittest.main()
