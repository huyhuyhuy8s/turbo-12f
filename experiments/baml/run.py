import enum
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from t12 import tools  # noqa: E402
from t12.errors import StateError  # noqa: E402


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
    except Exception as exc:
        if record is not None:
            record({"ok": False, "refused_by": "baml", "message": str(exc)})
        return False, str(exc)
    tool = call.pop("tool")
    try:
        tools.parse(json.dumps({"tool": tool, "args": call}), kind)
    except StateError as exc:
        message = str(exc)
        if record is not None:
            record({"ok": False, "refused_by": "core", "message": message})
        return False, message
    return act({"tool": tool, "args": call})
