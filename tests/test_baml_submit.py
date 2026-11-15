"""`submit` turns a BAML-format reply into one `t12 act` call."""

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


if __name__ == "__main__":
    unittest.main()
