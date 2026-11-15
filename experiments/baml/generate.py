from t12 import tools


BAML_TYPES = {tools.STR_LIST: "string[]"}

ALIASES = {
    "enums": {
        value.title().replace("-", ""): value
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
        f'  {value.title().replace("-", "")} @alias("{value}")\n' for value in values
    )
    return f"enum {name} {{\n" + members + "}\n"


def verdict_enum():
    return enum("Verdict", tools.TOOLS["review"]["review"]["verdict"])


def item_kind_enum():
    return enum("ItemKind", tools.ITEM_KINDS)


def render():
    return verdict_enum() + "\n" + item_kind_enum()
