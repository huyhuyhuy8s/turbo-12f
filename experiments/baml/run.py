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


def plain(value, keep_none=False):
    if isinstance(value, enum.Enum):
        return kebab(value.name)
    if isinstance(value, list):
        return [plain(v, keep_none) for v in value]
    if isinstance(value, dict):
        fields = value
    elif hasattr(value, "__dict__"):
        fields = vars(value)
    else:
        return value
    return {
        k.removesuffix("_"): plain(v, keep_none)
        for k, v in fields.items()
        if keep_none or v is not None
    }


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


def _unexpected(raw, call):
    if isinstance(raw, dict) and isinstance(call, dict):
        for key, value in raw.items():
            if key not in call:
                return key
            if value is None:
                continue
            found = _unexpected(value, call[key])
            if found is not None:
                return found
    elif isinstance(raw, list) and isinstance(call, list):
        for r, c in zip(raw, call):
            found = _unexpected(r, c)
            if found is not None:
                return found
    return None


def submit(reply, kind, parse, act, record=None):
    found = _groups(reply)
    if found > 1:
        return _refuse(record, "script", f"worker output must hold exactly one call; found {found}")
    try:
        parsed = parse(reply)
        call = plain(parsed)
        shape = plain(parsed, True)
    except Exception as exc:
        return _refuse(record, "baml", str(exc))
    try:
        raw, _ = json.JSONDecoder().raw_decode(reply, reply.index("{"))
    except ValueError:
        raw = {}
    if "args" not in call and isinstance(raw.get("args"), dict):
        args = {**raw["args"], **{k: v for k, v in raw.items() if k != "args"}}
    else:
        args = raw
    key = _unexpected({k: v for k, v in args.items() if k != "tool"}, shape)
    if key is not None:
        return _refuse(record, "script", f"unexpected arg '{key}'")
    tool = call.pop("tool")
    try:
        tools.parse(json.dumps({"tool": tool, "args": call}), kind)
    except StateError as exc:
        return _refuse(record, "core", str(exc))
    if record is not None:
        record({"ok": True})
    return act({"tool": tool, "args": call})


def main(argv, parse=None, act=None, run=None):
    if parse is not None:
        _, message = submit("", "review", parse, lambda call: (True, "ok"))
        print(message)
        return 0
    try:
        import baml_py  # noqa: F401
    except ImportError:
        print(
            "baml-py is not importable; run: pip install baml-py pydantic typing_extensions"
            " and then: baml-cli generate --from experiments/baml/baml_src",
            file=sys.stderr,
        )
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
