import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from t12 import tools  # noqa: E402


BAML_TYPES = {
    tools.STR_LIST: "string[]",
    tools.UNITS: "Unit[]",
    tools.INT: "int",
    tools.BOOL: "bool",
    tools.TEXT: "string",
    tools.INT_LIST: "int[]",
    tools.ITEMS: "TestItem[]",
    tools.UNIT_ITEMS: "UnitTestItem[]",
    tools.FINDINGS: "Finding[]",
    tools.TOOLS["review"]["review"]["verdict"]: "Verdict",
}


def member_name(value):
    return value.title().replace("-", "")


ALIASES = {
    "enums": {
        member_name(value): value
        for value in [*tools.TOOLS["review"]["review"]["verdict"], *tools.ITEM_KINDS]
    },
    "fields": {"class_": "class"},
}


def classes():
    return {
        class_name(name): {
            "tool": f'"{name}"',
            **{
                arg: BAML_TYPES.get(shape, shape)
                + ("?" if arg in tools.OPTIONAL_ARGS.get(name, ()) else "")
                for arg, shape in args.items()
            },
        }
        for kind in tools.TOOLS.values()
        for name, args in kind.items()
    }


def enum(name, values):
    members = "".join(
        f'  {member_name(value)} @alias("{value}")\n' for value in values
    )
    return f"enum {name} {{\n" + members + "}\n"


def verdict_enum():
    return enum("Verdict", tools.TOOLS["review"]["review"]["verdict"])


def item_kind_enum():
    return enum("ItemKind", tools.ITEM_KINDS)


def class_block(name, fields):
    body = "".join(
        f'  class_ {shape} @alias("class")\n' if arg == "class" else f"  {arg} {shape}\n"
        for arg, shape in fields.items()
    )
    return f"class {name} {{\n" + body + "}\n"


def class_name(tool):
    return tool.title().replace("_", "")


def answer_function(function, kind):
    union = " | ".join(class_name(tool) for tool in tools.TOOLS[kind])
    prompt = '  prompt #"\n    {{ ctx.output_format }}\n  "#\n'
    return f"function {function}(input: string) -> {union} {{\n  client Worker\n{prompt}}}\n"


def analyzer_answer():
    return answer_function("AnalyzerAnswer", "analyzer")


def red_answer():
    return answer_function("RedAnswer", "red")


def review_answer():
    return answer_function("ReviewAnswer", "review")


def client():
    return (
        "client<llm> Worker {\n"
        "  provider openai\n"
        "  options {\n"
        '    model "gpt-4o"\n'
        "  }\n"
        "}\n"
    )


def render():
    blocks = "\n".join(class_block(n, f) for n, f in classes().items())
    item_fields = {
        arg: "ItemKind" if shape == tools.ITEM_KINDS else BAML_TYPES.get(shape, shape)
        for arg, shape in tools.ITEM_FIELDS.items()
    }
    item = class_block("TestItem", item_fields)
    unit_item = class_block("UnitTestItem", {**item_fields, "unit": "string?"})
    unit = class_block("Unit", {
        arg: BAML_TYPES.get(shape, shape) for arg, shape in tools.UNIT_FIELDS.items()
    })
    finding = class_block("Finding", {f: tools.STR for f in tools.FINDING_FIELDS})
    return (verdict_enum() + "\n" + item_kind_enum() + "\n" + blocks + "\n" + item
            + "\n" + unit_item + "\n" + unit + "\n" + finding + "\n" + analyzer_answer()
            + "\n" + red_answer() + "\n" + review_answer() + "\n" + client())


if __name__ == "__main__":
    (Path(__file__).parent / "baml_src" / "tools.baml").write_bytes(render().encode())
