from t12 import tools


def classes():
    return {
        name.title().replace("_", ""): args
        for kind in tools.TOOLS.values()
        for name, args in kind.items()
    }
