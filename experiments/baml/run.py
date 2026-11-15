import enum


def plain(value):
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, list):
        return [plain(v) for v in value]
    if isinstance(value, dict):
        fields = value
    elif hasattr(value, "__dict__"):
        fields = vars(value)
    else:
        return value
    return {k: plain(v) for k, v in fields.items() if v is not None}


def submit(reply, kind, parse, act):
    call = plain(parse(reply))
    tool = call.pop("tool")
    return act({"tool": tool, "args": call})
