"""`main(["submit", ...])` reads the reply, calls `t12 act`, appends the evidence line."""

import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
import unittest.mock
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

    def test_parse_failure_appends_the_baml_refusal_evidence_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            reply = os.path.join(tmp, "reply.txt")
            with open(reply, "w") as fh:
                fh.write("not a call")
            home = os.path.join(tmp, "home")
            os.makedirs(home)
            evidence = os.path.join(tmp, "evidence.jsonl")

            def parse(text):
                raise ValueError("boom")

            with contextlib.redirect_stdout(io.StringIO()):
                run.main(
                    ["submit", "--kind", "review", "--reply-file", reply, "--home", home,
                     "--task-id", "T", "--role", "R", "--session", "S",
                     "--evidence", evidence],
                    parse=parse, act=lambda call: (True, "ok"))
            lines = []
            if os.path.exists(evidence):
                with open(evidence) as fh:
                    lines = fh.read().splitlines()
            self.assertEqual(
                ['{"arm": "B", "kind": "review", "ok": false, "refused_by": "baml", "error": "boom"}'],
                lines)


class SubmitActFailure(unittest.TestCase):
    def test_act_refusal_returns_1_and_prints_the_act_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            reply = os.path.join(tmp, "reply.txt")
            with open(reply, "w") as fh:
                fh.write("a call")
            home = os.path.join(tmp, "home")
            os.makedirs(home)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = run.main(
                    ["submit", "--kind", "review", "--reply-file", reply, "--home", home,
                     "--task-id", "T", "--role", "R", "--session", "S",
                     "--evidence", os.path.join(tmp, "evidence.jsonl")],
                    parse=lambda text: SimpleNamespace(tool="escalate", question="q"),
                    act=lambda call: (False, "refused: bad"))
            self.assertEqual((1, "refused: bad"), (code, out.getvalue().strip()))

    def test_act_refusal_appends_the_act_refusal_evidence_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            reply = os.path.join(tmp, "reply.txt")
            with open(reply, "w") as fh:
                fh.write("a call")
            home = os.path.join(tmp, "home")
            os.makedirs(home)
            evidence = os.path.join(tmp, "evidence.jsonl")
            with contextlib.redirect_stdout(io.StringIO()):
                run.main(
                    ["submit", "--kind", "review", "--reply-file", reply, "--home", home,
                     "--task-id", "T", "--role", "R", "--session", "S",
                     "--evidence", evidence],
                    parse=lambda text: SimpleNamespace(tool="escalate", question="q"),
                    act=lambda call: (False, "refused: bad"))
            lines = []
            if os.path.exists(evidence):
                with open(evidence) as fh:
                    lines = fh.read().splitlines()
            self.assertEqual(
                ['{"arm": "B", "kind": "review", "ok": false, "refused_by": "act", "error": "refused: bad"}'],
                lines)


class SubmitDefaultAct(unittest.TestCase):
    def test_default_act_writes_the_parsed_call_beside_the_reply_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            reply = os.path.join(tmp, "reply.txt")
            with open(reply, "w") as fh:
                fh.write("a call")
            home = os.path.join(tmp, "home")
            os.makedirs(home)
            with contextlib.redirect_stdout(io.StringIO()):
                run.main(
                    ["submit", "--kind", "review", "--reply-file", reply, "--home", home,
                     "--task-id", "T", "--role", "R", "--session", "S",
                     "--evidence", os.path.join(tmp, "evidence.jsonl")],
                    parse=lambda text: SimpleNamespace(tool="escalate", question="q"),
                    run=lambda argv: 0)
            written = None
            if os.path.exists(reply + ".call.json"):
                with open(reply + ".call.json") as fh:
                    written = json.load(fh)
            self.assertEqual({"tool": "escalate", "args": {"question": "q"}}, written)

    def test_default_act_passes_unit_and_run_id_to_t12_act(self):
        with tempfile.TemporaryDirectory() as tmp:
            reply = os.path.join(tmp, "reply.txt")
            with open(reply, "w") as fh:
                fh.write("a call")
            home = os.path.join(tmp, "home")
            os.makedirs(home)
            seen = []
            with contextlib.redirect_stdout(io.StringIO()):
                run.main(
                    ["submit", "--kind", "review", "--reply-file", reply, "--home", home,
                     "--task-id", "T", "--role", "R", "--session", "S",
                     "--unit", "U1", "--run-id", "R1",
                     "--evidence", os.path.join(tmp, "evidence.jsonl")],
                    parse=lambda text: SimpleNamespace(tool="escalate", question="q"),
                    run=lambda argv: seen.append(argv) or 0)
            expected = [os.path.join(ROOT, "bin", "t12"), "act", "--home", home,
                        "--task-id", "T", "--role", "R", "--session", "S",
                        "--input", reply + ".call.json", "--raw", reply,
                        "--parsed-by", "extracted", "--unit", "U1", "--run-id", "R1"]
            self.assertEqual([expected], seen)

    def test_default_act_omits_unit_and_run_id_flags_when_not_given(self):
        with tempfile.TemporaryDirectory() as tmp:
            reply = os.path.join(tmp, "reply.txt")
            with open(reply, "w") as fh:
                fh.write("a call")
            home = os.path.join(tmp, "home")
            os.makedirs(home)
            seen = []
            with contextlib.redirect_stdout(io.StringIO()):
                run.main(
                    ["submit", "--kind", "review", "--reply-file", reply, "--home", home,
                     "--task-id", "T", "--role", "R", "--session", "S",
                     "--evidence", os.path.join(tmp, "evidence.jsonl")],
                    parse=lambda text: SimpleNamespace(tool="escalate", question="q"),
                    run=lambda argv: seen.append(argv) or 0)
            flags = [[a for a in argv if a in ("--unit", "--run-id")] for argv in seen]
            self.assertEqual([[]], flags)


class SubmitWithoutBamlPy(unittest.TestCase):
    def test_missing_baml_py_returns_2_and_prints_the_package_and_both_commands(self):
        with tempfile.TemporaryDirectory() as tmp:
            reply = os.path.join(tmp, "reply.txt")
            with open(reply, "w") as fh:
                fh.write("a call")
            out = io.StringIO()
            with unittest.mock.patch.dict(sys.modules, {"baml_py": None}), \
                    contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                code = run.main(
                    ["submit", "--kind", "review", "--reply-file", reply,
                     "--home", tmp, "--task-id", "T", "--role", "R", "--session", "S",
                     "--evidence", os.path.join(tmp, "evidence.jsonl")])
            shown = out.getvalue()
            self.assertEqual((2, True, True, True), (code, "baml-py" in shown, "pip install" in shown, "baml-cli generate" in shown))

    def test_missing_baml_py_writes_no_evidence_file_and_no_call_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            reply = os.path.join(tmp, "reply.txt")
            evidence = os.path.join(tmp, "evidence.jsonl")
            with open(reply, "w") as fh:
                fh.write('{"tool": "escalate", "question": "q"}')
            with unittest.mock.patch.dict(sys.modules, {"baml_py": None}), \
                    contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                run.main(
                    ["submit", "--kind", "review", "--reply-file", reply,
                     "--home", tmp, "--task-id", "T", "--role", "R", "--session", "S",
                     "--evidence", evidence])
            self.assertEqual([False, False], [os.path.exists(evidence), os.path.exists(reply + ".call.json")])

class SubmitMissingReplyFile(unittest.TestCase):
    def test_missing_reply_file_returns_2_names_the_path_and_writes_no_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            reply = os.path.join(tmp, "absent-reply.txt")
            evidence = os.path.join(tmp, "evidence.jsonl")
            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                code = run.main(
                    ["submit", "--kind", "review", "--reply-file", reply,
                     "--home", tmp, "--task-id", "T", "--role", "R", "--session", "S",
                     "--evidence", evidence],
                    parse=lambda text: SimpleNamespace(tool="escalate", question="q"),
                    act=lambda call: (True, "ok"))
            shown = out.getvalue()
            self.assertEqual((2, True, False), (code, reply in shown, os.path.exists(evidence)))


class SubmitReadsReply(unittest.TestCase):
    def test_parse_receives_the_text_of_the_reply_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            reply = os.path.join(tmp, "reply.txt")
            with open(reply, "w") as fh:
                fh.write('{"tool": "escalate", "question": "q"}')
            seen = []

            def parse(text):
                seen.append(text)
                return SimpleNamespace(tool="escalate", question="q")

            with contextlib.redirect_stdout(io.StringIO()):
                run.main(
                    ["submit", "--kind", "review", "--reply-file", reply,
                     "--home", tmp, "--task-id", "T", "--role", "R", "--session", "S",
                     "--evidence", os.path.join(tmp, "evidence.jsonl")],
                    parse=parse, act=lambda call: (True, "ok"))
            self.assertEqual(['{"tool": "escalate", "question": "q"}'], seen)


class SubmitEvidenceOption(unittest.TestCase):
    def test_evidence_line_goes_to_the_evidence_option_wherever_it_sits(self):
        with tempfile.TemporaryDirectory() as tmp:
            reply = os.path.join(tmp, "reply.txt")
            with open(reply, "w") as fh:
                fh.write('{"tool": "escalate", "question": "q"}')
            evidence = os.path.join(tmp, "evidence.jsonl")
            with contextlib.redirect_stdout(io.StringIO()):
                run.main(
                    ["submit", "--evidence", evidence, "--kind", "review", "--reply-file", reply,
                     "--home", tmp, "--task-id", "T", "--role", "R",
                     "--session", os.path.join(tmp, "last-argument")],
                    parse=lambda text: SimpleNamespace(tool="escalate", question="q"),
                    act=lambda call: (True, "ok"))
            lines = []
            if os.path.exists(evidence):
                with open(evidence) as fh:
                    lines = fh.read().splitlines()
            self.assertEqual(
                ['{"arm": "B", "kind": "review", "ok": true, "refused_by": null, "error": null}'],
                lines)


class SubmitKindOption(unittest.TestCase):
    def test_kind_option_selects_the_tool_set_and_is_recorded_in_the_evidence_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            reply = os.path.join(tmp, "reply.txt")
            with open(reply, "w") as fh:
                fh.write('{"tool": "green_ready", "summary": "s"}')
            evidence = os.path.join(tmp, "evidence.jsonl")
            with contextlib.redirect_stdout(io.StringIO()):
                run.main(
                    ["submit", "--kind", "green", "--reply-file", reply,
                     "--home", tmp, "--task-id", "T", "--role", "R", "--session", "S",
                     "--evidence", evidence],
                    parse=lambda text: SimpleNamespace(tool="green_ready", summary="s"),
                    act=lambda call: (True, "ok"))
            lines = []
            if os.path.exists(evidence):
                with open(evidence) as fh:
                    lines = fh.read().splitlines()
            self.assertEqual(
                ['{"arm": "B", "kind": "green", "ok": true, "refused_by": null, "error": null}'],
                lines)


class SubmitNoReplyFileOption(unittest.TestCase):
    def test_absent_reply_file_option_returns_2_names_the_option_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            evidence = os.path.join(tmp, "evidence.jsonl")
            out = io.StringIO()
            calls = []
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                code = run.main(
                    ["submit", "--kind", "review",
                     "--home", tmp, "--task-id", "T", "--role", "R", "--session", "S",
                     "--evidence", evidence],
                    parse=lambda text: SimpleNamespace(tool="escalate", question="q"),
                    run=lambda argv: calls.append(argv) or 0)
            shown = out.getvalue()
            self.assertEqual((2, True, False, []),
                             (code, "--reply-file" in shown, os.path.exists(evidence), calls))

if __name__ == "__main__":
    unittest.main()
