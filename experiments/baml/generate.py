from t12 import tools


BAML_TYPES = {tools.STR_LIST: "string[]"}


def classes():
    return {
        name.title().replace("_", ""): {
            "tool": tools.STR,
            **{arg: BAML_TYPES.get(shape, shape) for arg, shape in args.items()},
        }
        for kind in tools.TOOLS.values()
        for name, args in kind.items()
    }


def verdict_enum():
    members = "".join(
        f'  {value.title().replace("-", "")} @alias("{value}")\n'
        for value in tools.TOOLS["review"]["review"]["verdict"]
    )
    return "enum Verdict {\n" + members + "}\n"


def render():
    return verdict_enum()
