"""The BAML source is generated from the tool registry; these tests keep
the two in step."""

import importlib.util
import os
import re
import subprocess
import sys
import unittest
from unittest import mock

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

    def test_integer_and_boolean_args_map_to_baml_int_and_bool(self):
        classes = generate.classes()
        found = {"last_assertion_line": classes["RedTestReady"]["last_assertion_line"],
                 "changed": classes["RefactorDone"]["changed"]}
        self.assertEqual({"last_assertion_line": "int", "changed": "bool"}, found)

    def test_integer_list_and_maybe_empty_string_item_fields_map_to_baml_types(self):
        block = re.search(r"class TestItem\s*\{(.*?)\}", generate.render(), re.S)
        fields = dict(re.findall(r"^\s*(\w+)\s+(.*?)\s*$", block.group(1) if block else "", re.M))
        found = {"criteria": fields.get("criteria"), "needed": fields.get("needed")}
        self.assertEqual({"criteria": "int[]", "needed": "string"}, found)

    def test_review_findings_and_verdict_args_map_to_finding_list_and_verdict_enum(self):
        review = generate.classes()["Review"]
        found = {"findings": review["findings"], "verdict": review["verdict"]}
        self.assertEqual({"findings": "Finding[]", "verdict": "Verdict"}, found)

    def test_item_list_args_map_to_test_item_and_unit_test_item_lists(self):
        classes = generate.classes()
        found = {"add_items": classes["RedTestReady"]["add_items"],
                 "items": classes["TestListReady"]["items"]}
        self.assertEqual({"add_items": "TestItem[]", "items": "UnitTestItem[]"}, found)


class EnumRendering(unittest.TestCase):
    def test_verdict_enum_has_one_aliased_member_per_review_verdict(self):
        block = re.search(r"enum Verdict\s*\{(.*?)\}", generate.render(), re.S)
        lines = [ln.strip() for ln in (block.group(1) if block else "").splitlines()
                 if ln.strip()]
        aliases = [(re.search(r'@alias\("([^"]*)"\)', ln) or [None, None])[1]
                   for ln in lines]
        expected = sorted(tools.TOOLS["review"]["review"]["verdict"])
        self.assertEqual(expected, sorted(aliases, key=str))

    def test_item_kind_enum_has_one_aliased_member_per_item_kind(self):
        block = re.search(r"enum ItemKind\s*\{(.*?)\}", generate.render(), re.S)
        lines = [ln.strip() for ln in (block.group(1) if block else "").splitlines()
                 if ln.strip()]
        aliases = [(re.search(r'@alias\("([^"]*)"\)', ln) or [None, None])[1]
                   for ln in lines]
        self.assertEqual(sorted(tools.ITEM_KINDS), sorted(aliases, key=str))

    def test_aliases_enums_maps_each_member_name_to_its_value(self):
        rendered = generate.render()
        members = re.findall(r'^\s+(\w+)\s+@alias\("([^"]*)"\)\s*$', rendered, re.M)
        self.assertEqual(dict(members), generate.ALIASES["enums"])


class OptionalAndEdgeRendering(unittest.TestCase):
    def test_units_arg_of_test_list_ready_renders_as_optional_unit_list(self):
        block = re.search(r"class TestListReady\s*\{(.*?)\}", generate.render(), re.S)
        field = re.search(r"^\s*units\s+(.*?)\s*$", block.group(1) if block else "", re.M)
        self.assertEqual("Unit[]?", field.group(1) if field else None)

    def test_unit_test_item_has_an_optional_unit_string(self):
        block = re.search(r"class UnitTestItem\s*\{(.*?)\}", generate.render(), re.S)
        field = re.search(r"^\s*unit\s+(.*?)\s*$", block.group(1) if block else "", re.M)
        self.assertEqual("string?", field.group(1) if field else None)

    def test_unit_test_item_extends_test_item_and_unit_class_is_defined(self):
        def field_names(name):
            block = re.search(r"class %s\s*\{(.*?)\}" % name, generate.render(), re.S)
            return re.findall(r"^\s*(\w+)\s", block.group(1) if block else "", re.M)
        expected = ([*field_names("TestItem"), "unit"], list(tools.UNIT_FIELDS))
        self.assertEqual(expected, (field_names("UnitTestItem"), field_names("Unit")))

    def test_test_item_exists_without_a_unit_field(self):
        block = re.search(r"class TestItem\s*\{(.*?)\}", generate.render(), re.S)
        names = re.findall(r"^\s*(\w+)\s", block.group(1) if block else "", re.M)
        self.assertEqual((True, False), (block is not None, "unit" in names))

    def test_finding_class_field_renders_with_a_keyword_safe_name_and_alias(self):
        block = re.search(r"class Finding\s*\{(.*?)\}", generate.render(), re.S)
        lines = [ln.strip() for ln in (block.group(1) if block else "").splitlines()]
        self.assertIn('class_ string @alias("class")', lines)

    def test_aliases_map_the_keyword_safe_field_name_back_to_class(self):
        self.assertEqual({"class_": "class"}, generate.ALIASES["fields"])


class AnswerFunctions(unittest.TestCase):
    def test_analyzer_answer_returns_union_of_exactly_the_analyzer_tool_classes(self):
        head = re.search(r"function\s+AnalyzerAnswer\s*\([^)]*\)\s*->\s*([^{]*?)\s*\{",
                         generate.render())
        found = sorted(part.strip() for part in head.group(1).split("|")) if head else []
        expected = sorted(pascal(name) for name in tools.TOOLS["analyzer"])
        self.assertEqual(expected, found)

    def test_red_answer_returns_union_of_exactly_the_red_tool_classes(self):
        head = re.search(r"function\s+RedAnswer\s*\([^)]*\)\s*->\s*([^{]*?)\s*\{",
                         generate.render())
        found = sorted(part.strip() for part in head.group(1).split("|")) if head else []
        expected = sorted(pascal(name) for name in tools.TOOLS["red"])
        self.assertEqual(expected, found)

    def test_review_answer_returns_union_of_exactly_the_review_tool_classes(self):
        head = re.search(r"function\s+ReviewAnswer\s*\([^)]*\)\s*->\s*([^{]*?)\s*\{",
                         generate.render())
        found = sorted(part.strip() for part in head.group(1).split("|")) if head else []
        expected = sorted(pascal(name) for name in tools.TOOLS["review"])
        self.assertEqual(expected, found)

    def test_each_answer_function_prompt_contains_the_output_format_placeholder(self):
        functions = re.findall(r"function\s+(\w+)\s*\([^)]*\)\s*->[^{]*\{(.*?)\n\}",
                               generate.render(), re.DOTALL)
        found = sorted(name for name, body in functions
                       if "{{ ctx.output_format }}" in body)
        self.assertEqual(["AnalyzerAnswer", "RedAnswer", "ReviewAnswer"], found)


class Drift(unittest.TestCase):
    def test_render_equals_the_committed_tools_baml(self):
        path = os.path.join(ROOT, "experiments", "baml", "baml_src", "tools.baml")
        committed = open(path).read() if os.path.exists(path) else ""
        self.assertEqual(committed, generate.render())

    def test_render_differs_from_committed_when_review_gains_an_arg(self):
        path = os.path.join(ROOT, "experiments", "baml", "baml_src", "tools.baml")
        committed = open(path).read()
        extra = {**tools.TOOLS["review"]["review"], "extra_arg": tools.STR}
        with mock.patch.dict(tools.TOOLS["review"], {"review": extra}):
            patched = generate.render()
        self.assertNotEqual(committed, patched)

    def test_render_differs_from_committed_when_a_tool_is_added(self):
        path = os.path.join(ROOT, "experiments", "baml", "baml_src", "tools.baml")
        committed = open(path).read()
        with mock.patch.dict(tools.TOOLS["green"], {"extra_tool": {"note": tools.STR}}):
            patched = generate.render()
        self.assertNotEqual(committed, patched)

    def test_running_generate_as_a_script_rewrites_the_committed_file_with_render(self):
        path = os.path.join(ROOT, "experiments", "baml", "baml_src", "tools.baml")
        original = open(path).read()
        try:
            with open(path, "w") as handle:
                handle.write("stale\n")
            proc = subprocess.run(
                [sys.executable, "experiments/baml/generate.py"],
                cwd=ROOT, capture_output=True, text=True)
            written = open(path).read()
        finally:
            with open(path, "w") as handle:
                handle.write(original)
        self.assertEqual((0, generate.render()), (proc.returncode, written), proc.stderr)


if __name__ == "__main__":
    unittest.main()
