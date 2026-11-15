from t12 import tools


BAML_TYPES = {tools.STR_LIST: "string[]", tools.UNITS: "Unit[]?"}


def member_name(value):
    return value.title().replace("-", "")


ALIASES = {
    "enums": {
        member_name(value): value
        for value in [*tools.TOOLS["review"]["review"]["verdict"], *tools.ITEM_KINDS]
    }
}


def classes():
    return {
        name.title().replace("_", ""): {
            "tool": tools.STR,
            **{arg: BAML_TYPES.get(shape, shape) for arg, shape in args.items()},
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
    body = "".join(f"  {arg} {shape}\n" for arg, shape in fields.items())
    return f"class {name} {{\n" + body + "}\n"


def render():
    blocks = "\n".join(class_block(n, f) for n, f in classes().items())
    unit_item = class_block("UnitTestItem", {"unit": "string?"})
    return verdict_enum() + "\n" + item_kind_enum() + "\n" + blocks + "\n" + unit_item
