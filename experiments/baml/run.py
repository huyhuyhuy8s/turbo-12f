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


def _refuse(record, refused_by, message):
    if record is not None:
        record({"ok": False, "refused_by": refused_by, "message": message})
    return False, message


def _groups(reply):
    depth = found = 0
    in_str = escaped = False
    for ch in reply:
        if in_str:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_str = False
        elif ch == '"' and depth:
            in_str = True
        elif ch == "{":
            found += depth == 0
            depth += 1
        elif ch == "}" and depth:
            depth -= 1
    return found


def submit(reply, kind, parse, act, record=None):
    found = _groups(reply)
    if found > 1:
        return _refuse(record, "script", f"worker output must hold exactly one call; found {found}")
    try:
        call = plain(parse(reply))
    except Exception as exc:
        return _refuse(record, "baml", str(exc))
    try:
        raw = json.loads(reply[reply.index("{") : reply.rindex("}") + 1])
    except ValueError:
        raw = {}
    args = raw.get("args") if "args" not in call and isinstance(raw.get("args"), dict) else raw
    for key, value in args.items():
        if key != "tool" and value is not None and key not in call:
            return _refuse(record, "script", f"unexpected arg '{key}'")
    tool = call.pop("tool")
    try:
        tools.parse(json.dumps({"tool": tool, "args": call}), kind)
    except StateError as exc:
        return _refuse(record, "core", str(exc))
    return act({"tool": tool, "args": call})


def main(argv):
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
