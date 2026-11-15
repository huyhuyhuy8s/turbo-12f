import enum
import json
import re
import subprocess
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


def _raw_args(reply, call):
    try:
        raw, _ = json.JSONDecoder().raw_decode(reply, reply.index("{"))
    except ValueError:
        raw = {}
    if "args" not in call and isinstance(raw.get("args"), dict):
        return {**raw["args"], **{k: v for k, v in raw.items() if k != "args"}}
    return raw


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
    args = _raw_args(reply, call)
    key = _unexpected({k: v for k, v in args.items() if k != "tool"}, shape)
    if key is not None:
        return _refuse(record, "script", f"unexpected arg '{key}'")
    tool = call.pop("tool")
    try:
        tools.parse(json.dumps({"tool": tool, "args": call}), kind)
    except StateError as exc:
        return _refuse(record, "core", str(exc))
    result = act({"tool": tool, "args": call})
    if isinstance(result, tuple) and not result[0]:
        _refuse(record, "act", result[1])
    elif record is not None:
        record({"ok": True})
    return result


def _option(argv, name):
    return argv[argv.index(name) + 1] if name in argv else None


def _execute(argv):
    return subprocess.run(argv).returncode


def _t12_act(argv, raw, run):
    def act(call):
        call_file = raw + ".call.json"
        with open(call_file, "w") as handle:
            handle.write(json.dumps(call))
        command = [
            str(Path(__file__).resolve().parents[2] / "bin" / "t12"),
            "act",
            "--home", _option(argv, "--home"),
            "--task-id", _option(argv, "--task-id"),
            "--role", _option(argv, "--role"),
            "--session", _option(argv, "--session"),
            "--input", call_file,
            "--raw", raw,
            "--parsed-by", "extracted",
        ]
        for flag in ("--unit", "--run-id"):
            value = _option(argv, flag)
            if value is not None:
                command += [flag, value]
        code = run(command)
        return code == 0, "" if code == 0 else f"t12 act exited {code}"

    return act


def main(argv, parse=None, act=None, run=None):
    if parse is not None:
        raw = _option(argv, "--reply-file")
        if raw is None:
            print("missing required option: --reply-file")
            return 2
        if not Path(raw).exists():
            print(f"reply file not found: {raw}")
            return 2

        if act is None:
            act = _t12_act(argv, raw, run or _execute)

        kind = _option(argv, "--kind") or "review"

        def forward(call):
            result = act(call)
            return result if isinstance(result, tuple) else (True, "ok")

        def record(outcome):
            path = _option(argv, "--evidence")
            if path is not None:
                line = {
                    "arm": "B",
                    "kind": kind,
                    "ok": outcome["ok"],
                    "refused_by": outcome.get("refused_by"),
                    "error": outcome.get("message"),
                }
                with open(path, "a") as evidence:
                    evidence.write(json.dumps(line) + "\n")

        reply = Path(raw).read_text()
        ok, message = submit(reply, kind, parse, forward, record)
        print(message)
        return 0 if ok else 1
    try:
        import baml_py  # noqa: F401
    except ImportError:
        print(
            "baml-py is not importable; run: pip install baml-py pydantic typing_extensions"
            " and then: baml-cli generate --from experiments/baml/baml_src"
        )
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
