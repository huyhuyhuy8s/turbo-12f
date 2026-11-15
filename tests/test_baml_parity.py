"""The BAML source is generated from the tool registry; these tests keep
the two in step."""

import importlib.util
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from t12 import tools  # noqa: E402

_SPEC = importlib.util.spec_from_file_location(
    "baml_generate", os.path.join(ROOT, "experiments", "baml", "generate.py"))
generate = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(generate)


def pascal(name):
    return "".join(part.capitalize() for part in name.split("_"))


class ToolClassNames(unittest.TestCase):
    def test_classes_has_one_pascal_case_class_per_tool(self):
        expected = sorted(pascal(name) for name in tools.ALL_TOOLS)
        found = sorted(set(expected) & set(generate.classes()))
        self.assertEqual(expected, found)

    def test_every_tool_class_has_a_tool_field(self):
        classes = generate.classes()
        missing = sorted(pascal(name) for name in tools.ALL_TOOLS
                         if "tool" not in classes.get(pascal(name), {}))
        self.assertEqual([], missing)

    def test_every_tool_class_has_exactly_its_args_as_fields(self):
        classes = generate.classes()
        expected = {pascal(name): sorted(args)
                    for kind in tools.TOOLS.values()
                    for name, args in kind.items()}
        found = {cls: sorted(set(classes.get(cls, {})) - {"tool"})
                 for cls in expected}
        self.assertEqual(expected, found)

    def test_arg_field_types_follow_the_type_mapping(self):
        classes = generate.classes()
        found = {"gaps": classes["TestListReady"]["gaps"],
                 "reason": classes["NoCheatLeft"]["reason"]}
        self.assertEqual({"gaps": "string[]", "reason": "string"}, found)


class EnumRendering(unittest.TestCase):
    def test_verdict_enum_has_one_aliased_member_per_review_verdict(self):
        block = re.search(r"enum Verdict\s*\{(.*?)\}", generate.render(), re.S)
        lines = [ln.strip() for ln in (block.group(1) if block else "").splitlines()
                 if ln.strip()]
        aliases = [(re.search(r'@alias\("([^"]*)"\)', ln) or [None, None])[1]
                   for ln in lines]
        expected = sorted(tools.TOOLS["review"]["review"]["verdict"])
        self.assertEqual(expected, sorted(aliases, key=str))


if __name__ == "__main__":
    unittest.main()
