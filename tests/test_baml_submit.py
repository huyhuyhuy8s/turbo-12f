"""`submit` turns a BAML-format reply into one `t12 act` call."""

import enum
import importlib.util
import json
import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from t12 import tools  # noqa: E402

_SPEC = importlib.util.spec_from_file_location(
    "baml_run", os.path.join(ROOT, "experiments", "baml", "run.py"))
run = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(run)


class ParseAnalyzerReply(unittest.TestCase):
    def test_analyzer_reply_with_units_none_acts_once_with_tool_and_args_without_units(self):
        reply = ('Here is the list: {"tool": "test_list_ready", "items": [], '
                 '"gaps": ["g"], "directives": ["d"], "units": null}')
        parsed = SimpleNamespace(tool="test_list_ready", items=[], gaps=["g"],
                                 directives=["d"], units=None)
        act = mock.Mock()
        run.submit(reply, "analyzer", lambda text: parsed, act)
        expected = {"tool": "test_list_ready",
                    "args": {"items": [], "gaps": ["g"], "directives": ["d"]}}
        self.assertEqual([mock.call(expected)], act.call_args_list)


class ParseReviewReply(unittest.TestCase):
    def test_review_reply_renames_class_field_and_enum_member_to_their_aliases(self):
        class Verdict(enum.Enum):
            MergeReady = "MergeReady"

        reply = '{"tool": "review", "verdict": "merge-ready"}'
        finding = SimpleNamespace(id="F1", class_="bug", severity="low",
                                  where="a.py", finding="f", owner="green")
        parsed = SimpleNamespace(tool="review", verdict=Verdict.MergeReady,
                                 findings=[finding], verification="ran tests")
        act = mock.Mock()
        run.submit(reply, "review", lambda text: parsed, act)
        expected = {"tool": "review", "args": {
            "verdict": "merge-ready",
            "findings": [{"id": "F1", "class": "bug", "severity": "low",
                          "where": "a.py", "finding": "f", "owner": "green"}],
            "verification": "ran tests"}}
        self.assertEqual([mock.call(expected)], act.call_args_list)


class ParseRedReply(unittest.TestCase):
    def test_red_reply_converts_enum_kind_inside_add_items(self):
        class ItemKind(enum.Enum):
            Example = "Example"

        reply = '{"tool": "red_test_ready", "item_id": "T1"}'
        item = SimpleNamespace(text="t", kind=ItemKind.Example, source="s",
                               needed="n", criteria=[0])
        parsed = SimpleNamespace(tool="red_test_ready", item_id="new",
                                 test_file="tests/a.py", test_name="test_a",
                                 last_assertion_line=7, defeats="d",
                                 add_items=[item])
        act = mock.Mock()
        run.submit(reply, "red", lambda text: parsed, act)
        expected = {"tool": "red_test_ready", "args": {
            "item_id": "new", "test_file": "tests/a.py", "test_name": "test_a",
            "last_assertion_line": 7, "defeats": "d",
            "add_items": [{"text": "t", "kind": "example", "source": "s",
                           "needed": "n", "criteria": [0]}]}}
        self.assertEqual([mock.call(expected)], act.call_args_list)


class ParseRaises(unittest.TestCase):
    def test_parse_error_is_refused_by_baml_with_its_text_and_act_is_not_called(self):
        def parse(text):
            raise ValueError("boom")

        act = mock.Mock()
        lines = []
        run.submit("not a call", "review", parse, act, record=lines.append)
        self.assertEqual([], act.call_args_list)
        self.assertEqual([{"ok": False, "refused_by": "baml", "message": "boom"}], lines)


class CoreRefusal(unittest.TestCase):
    def test_review_with_empty_verification_is_refused_by_core_and_act_is_not_called(self):
        reply = '{"tool": "review", "verdict": "merge-ready"}'
        parsed = SimpleNamespace(tool="review", verdict="merge-ready",
                                 findings=[], verification="")
        act = mock.Mock()
        lines = []
        run.submit(reply, "review", lambda text: parsed, act, record=lines.append)
        message = "invalid 'review' call: verification must be a non-empty string"
        self.assertEqual(
            ([], [{"ok": False, "refused_by": "core", "message": message}]),
            (act.call_args_list, lines))


class TwoCalls(unittest.TestCase):
    def test_reply_with_two_groups_is_refused_by_script_and_act_is_not_called(self):
        call = '{"tool": "test_list_ready", "items": [], "gaps": ["g"], "directives": ["d"]}'
        reply = call + " " + call
        parsed = SimpleNamespace(tool="test_list_ready", items=[], gaps=["g"],
                                 directives=["d"], units=None)
        act = mock.Mock()
        lines = []
        run.submit(reply, "analyzer", lambda text: parsed, act, record=lines.append)
        message = "worker output must hold exactly one call; found 2"
        self.assertEqual(
            ([], [{"ok": False, "refused_by": "script", "message": message}]),
            (act.call_args_list, lines))


class BraceInsideString(unittest.TestCase):
    def test_reply_with_braces_inside_a_string_is_one_group_and_acts_once(self):
        reply = '{"tool": "test_list_ready", "items": [], "gaps": ["}{"], "directives": ["d"]}'
        parsed = SimpleNamespace(tool="test_list_ready", items=[], gaps=["}{"],
                                 directives=["d"], units=None)
        act = mock.Mock()
        run.submit(reply, "analyzer", lambda text: parsed, act)
        self.assertEqual(1, act.call_count)


class ExtraArg(unittest.TestCase):
    def test_reply_key_missing_from_parse_result_is_refused_by_script_and_act_is_not_called(self):
        reply = ('{"tool": "test_list_ready", "items": [], "gaps": ["g"], '
                 '"directives": ["d"], "bonus": 1}')
        parsed = SimpleNamespace(tool="test_list_ready", items=[], gaps=["g"],
                                 directives=["d"], units=None)
        act = mock.Mock()
        lines = []
        run.submit(reply, "analyzer", lambda text: parsed, act, record=lines.append)
        message = "unexpected arg 'bonus'"
        self.assertEqual(
            ([], [{"ok": False, "refused_by": "script", "message": message}]),
            (act.call_args_list, lines))

if __name__ == "__main__":
    unittest.main()
