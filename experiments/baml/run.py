import enum
import re


def kebab(name):
    return re.sub(r"(?<!^)(?=[A-Z])", "-", name).lower()


def plain(value):
    if isinstance(value, enum.Enum):
        return kebab(value.name)
    if isinstance(value, list):
        return [plain(v) for v in value]
    if isinstance(value, dict):
        fields = value
    elif hasattr(value, "__dict__"):
        fields = vars(value)
    else:
        return value
    return {k.removesuffix("_"): plain(v) for k, v in fields.items() if v is not None}


def submit(reply, kind, parse, act, record=None):
    try:
        call = plain(parse(reply))
    except Exception:
        return None
    tool = call.pop("tool")
    return act({"tool": tool, "args": call})
