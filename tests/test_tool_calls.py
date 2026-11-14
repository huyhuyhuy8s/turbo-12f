"""The tool-call registry: which tools each role kind may call, how a
call is checked, and the schemas hosts register for them."""

import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from t12 import tools  # noqa: E402
from t12.errors import StateError  # noqa: E402

CALL = {"tool": "green_ready", "args": {"summary": "done"}}
TEXT = json.dumps(CALL)

ITEM = {"text": "greets by name", "kind": "behavior", "source": "AC 1", "needed": "a greet()",
        "criteria": [0]}


def call(tool, **args):
    return json.dumps({"tool": tool, "args": args})


class Registry(unittest.TestCase):
    def test_the_kinds_and_their_tools(self):
        self.assertEqual(tools.KINDS, ("analyzer", "red", "green", "refactor", "review"))
        self.assertEqual({kind: sorted(specs) for kind, specs in tools.TOOLS.items()}, {
            "analyzer": ["escalate", "test_list_ready"],
            "red": ["escalate", "no_cheat_left", "red_test_ready"],
            "green": ["escalate", "green_ready"],
            "refactor": ["escalate", "refactor_done"],
            "review": ["escalate", "review"],
        })
        for gone in ("implementation_ready", "qa_report"):
            self.assertNotIn(gone, tools.ALL_TOOLS)

    def test_a_tool_of_another_kind_is_refused(self):
        with self.assertRaises(StateError) as ctx:
            tools.parse(call("green_ready", summary="x"), "red")
        self.assertIn("not allowed for a red role", str(ctx.exception))

    def test_a_call_of_every_kind_parses(self):
        calls = {
            "analyzer": call("test_list_ready", items=[ITEM], gaps=[], directives=["split it"]),
            "red": call("red_test_ready", item_id="T1", test_file="tests/test_a.py",
                        test_name="test_a", last_assertion_line=12, defeats="a constant",
                        add_items=[ITEM]),
            "green": TEXT,
            "refactor": call("refactor_done", summary="renamed", changed=True),
            "review": call("review", verdict="merge-ready", findings=[], verification="ran"),
        }
        for kind, text in calls.items():
            tool, args = tools.parse(text, kind)
            self.assertEqual(json.loads(text), {"tool": tool, "args": args}, kind)
        self.assertEqual(tools.parse(call("no_cheat_left", reason="all covered"), "red")[0],
                         "no_cheat_left")
        self.assertEqual(tools.parse(call("escalate", question="which?"), "refactor")[0],
                         "escalate")

    def test_scalar_shapes_are_checked(self):
        base = {"item_id": "T1", "test_file": "t.py", "test_name": "n",
                "last_assertion_line": 3, "defeats": "d", "add_items": []}
        for bad in ({"last_assertion_line": "3"}, {"last_assertion_line": True},
                    {"last_assertion_line": 3.5}, {"item_id": ""}, {"add_items": None}):
            with self.assertRaises(StateError, msg=bad):
                tools.parse(call("red_test_ready", **dict(base, **bad)), "red")
        for bad in (1, "true", None):
            with self.assertRaises(StateError, msg=bad):
                tools.parse(call("refactor_done", summary="x", changed=bad), "refactor")
        tools.parse(call("refactor_done", summary="x", changed=False), "refactor")

    def test_a_missing_or_unexpected_arg_is_listed(self):
        with self.assertRaises(StateError) as ctx:
            tools.parse(call("test_list_ready", items=[], extra=1), "analyzer")
        message = str(ctx.exception)
        self.assertIn("missing arg 'gaps'", message)
        self.assertIn("missing arg 'directives'", message)
        self.assertIn("unexpected arg 'extra'", message)


class Items(unittest.TestCase):
    def parse_items(self, items, tool="test_list_ready"):
        if tool == "test_list_ready":
            return tools.parse(call(tool, items=items, gaps=[], directives=[]), "analyzer")
        return tools.parse(call(tool, item_id="T1", test_file="t.py", test_name="n",
                                last_assertion_line=1, defeats="d", add_items=items), "red")

    def problems(self, items, tool="test_list_ready"):
        with self.assertRaises(StateError) as ctx:
            self.parse_items(items, tool)
        return str(ctx.exception)

    def test_an_item_of_every_kind_value_is_accepted(self):
        for kind in tools.ITEM_KINDS:
            self.parse_items([dict(ITEM, kind=kind)])
        self.assertEqual(tools.ITEM_KINDS, ("behavior", "example", "edge", "error"))
        self.parse_items([dict(ITEM, needed="", criteria=[])])
        self.parse_items([])

    def test_the_enum_is_enforced_on_every_item(self):
        message = self.problems([ITEM, dict(ITEM, kind="smoke")])
        self.assertIn("items[1].kind must be one of", message)
        self.assertNotIn("items[0]", message)

    def test_every_required_field_is_named_when_missing(self):
        for field in tools.ITEM_FIELDS:
            item = {k: v for k, v in ITEM.items() if k != field}
            self.assertIn(f"items[0] is missing {field!r}", self.problems([item]), field)

    def test_an_unexpected_field_is_refused(self):
        self.assertIn("items[0] has unexpected 'id'", self.problems([dict(ITEM, id="T9")]))

    def test_field_shapes_are_checked(self):
        for bad in ({"text": ""}, {"text": "  "}, {"source": ""}, {"needed": None},
                    {"criteria": "0"}, {"criteria": ["0"]}, {"criteria": [True]},
                    {"criteria": [1.5]}):
            self.assertIn("items[0]", self.problems([dict(ITEM, **bad)]), bad)

    def test_an_item_that_is_not_an_object_or_a_list_that_is_not_a_list(self):
        self.assertIn("items[0] must be an object", self.problems(["greets"]))
        self.assertIn("list of test items", self.problems({"text": "x"}))

    def test_red_add_items_are_validated_the_same_way(self):
        message = self.problems([dict(ITEM, kind="smoke")], tool="red_test_ready")
        self.assertIn("add_items[0].kind must be one of", message)

    def test_every_bad_item_is_reported_at_once(self):
        message = self.problems([dict(ITEM, kind="x"), dict(ITEM, text="")])
        self.assertIn("items[0].kind", message)
        self.assertIn("items[1].text", message)


UNIT = {"id": "U1", "owns": ["src/a.py"], "tests": ["tests/test_a.py"], "after": [],
        "needed": ""}


class Units(unittest.TestCase):
    def parse(self, **extra):
        return tools.parse(call("test_list_ready", items=[dict(ITEM, unit="U1")], gaps=[],
                                directives=[], **extra), "analyzer")

    def problems(self, **extra):
        with self.assertRaises(StateError) as ctx:
            self.parse(**extra)
        return str(ctx.exception)

    def test_units_are_optional_and_so_is_an_items_unit(self):
        tools.parse(call("test_list_ready", items=[ITEM], gaps=[], directives=[]), "analyzer")
        _, args = self.parse(units=[UNIT, dict(UNIT, id="U2", needed="own module")])
        self.assertEqual([u["id"] for u in args["units"]], ["U1", "U2"])
        self.assertEqual(args["items"][0]["unit"], "U1")

    def test_a_unit_has_exactly_its_fields(self):
        for field in tools.UNIT_FIELDS:
            unit = {k: v for k, v in UNIT.items() if k != field}
            self.assertIn(f"units[0] is missing {field!r}", self.problems(units=[unit]), field)
        self.assertIn("units[0] has unexpected 'glob'", self.problems(units=[dict(UNIT, glob="*")]))

    def test_unit_field_shapes_are_checked(self):
        for bad in ({"id": ""}, {"owns": "a.py"}, {"tests": [1]}, {"after": None}, {"needed": 3}):
            self.assertIn("units[0]", self.problems(units=[dict(UNIT, **bad)]), bad)
        self.assertIn("must be a list of units", self.problems(units={"id": "U1"}))

    def test_red_may_not_name_a_unit_in_its_added_items(self):
        message = ""
        try:
            tools.parse(call("red_test_ready", item_id="T1", test_file="t.py", test_name="n",
                             last_assertion_line=1, defeats="d",
                             add_items=[dict(ITEM, unit="U1")]), "red")
        except StateError as exc:
            message = str(exc)
        self.assertIn("add_items[0] has unexpected 'unit'", message)


class Schema(unittest.TestCase):
    def test_every_tool_and_arg_of_a_kind_is_in_its_schema(self):
        for kind, specs in tools.TOOLS.items():
            schema = tools.json_schema(kind)
            self.assertEqual(schema["type"], "object")
            self.assertEqual(schema["properties"]["tool"]["enum"], sorted(specs))
            args = [sorted(a["required"]) for a in schema["properties"]["args"]["anyOf"]]
            required = [sorted(k for k in spec if k not in tools.OPTIONAL_ARGS.get(name, ()))
                        for name, spec in specs.items()]
            self.assertEqual(sorted(args), sorted(required))
            every = [sorted(a["properties"]) for a in schema["properties"]["args"]["anyOf"]]
            self.assertEqual(sorted(every), sorted(sorted(spec) for spec in specs.values()))

    def test_an_enum_arg_is_closed_to_its_values(self):
        review = tools.json_schema("review")["properties"]["args"]["anyOf"]
        [args] = [a for a in review if "verdict" in a["properties"]]
        self.assertEqual(args["properties"]["verdict"]["enum"],
                         list(tools.TOOLS["review"]["review"]["verdict"]))

    def test_item_ints_and_bools_have_json_types(self):
        [analyzer] = [a for a in tools.json_schema("analyzer")["properties"]["args"]["anyOf"]
                      if "items" in a["properties"]]
        items = analyzer["properties"]["items"]
        self.assertEqual(items["type"], "array")
        item = items["items"]
        self.assertEqual(item["required"], list(tools.ITEM_FIELDS))
        self.assertEqual(sorted(item["properties"]), sorted([*tools.ITEM_FIELDS, "unit"]))
        self.assertFalse(item["additionalProperties"])
        self.assertEqual(item["properties"]["kind"]["enum"], list(tools.ITEM_KINDS))
        self.assertEqual(item["properties"]["criteria"],
                         {"type": "array", "items": {"type": "integer"}})
        [red] = [a for a in tools.json_schema("red")["properties"]["args"]["anyOf"]
                 if "last_assertion_line" in a["properties"]]
        self.assertEqual(red["properties"]["last_assertion_line"], {"type": "integer"})
        red_item = {**item, "properties": {k: v for k, v in item["properties"].items()
                                           if k != "unit"}}
        self.assertEqual(red["properties"]["add_items"]["items"], red_item)
        [refactor] = [a for a in tools.json_schema("refactor")["properties"]["args"]["anyOf"]
                      if "changed" in a["properties"]]
        self.assertEqual(refactor["properties"]["changed"], {"type": "boolean"})

    def test_schema_for_and_host_tools_cover_every_kind(self):
        for kind, specs in tools.TOOLS.items():
            self.assertEqual([t["name"] for t in tools.schema_for(kind)], list(specs))
            hosts = tools.host_tools(kind)
            self.assertEqual([t["name"] for t in hosts], list(specs))
            for host in hosts:
                optional = tools.OPTIONAL_ARGS.get(host["name"], ())
                self.assertEqual(sorted(host["parameters"]["required"]),
                                 sorted(k for k in specs[host["name"]] if k not in optional))
                self.assertEqual(sorted(host["parameters"]["properties"]),
                                 sorted(specs[host["name"]]))
        json.dumps([tools.schema_for(k) for k in tools.KINDS])
        [analyzer] = [t for t in tools.schema_for("analyzer") if t["name"] == "test_list_ready"]
        self.assertEqual(analyzer["args"]["items"][tools.UNIT_ITEMS]["kind"],
                         list(tools.ITEM_KINDS))
        self.assertEqual(analyzer["args"]["units"][tools.UNITS]["owns"], "list of strings")


if __name__ == "__main__":
    unittest.main()
