from t12 import tools


def classes():
    return {
        name.title().replace("_", ""): {"tool": tools.STR, **args}
        for kind in tools.TOOLS.values()
        for name, args in kind.items()
    }
