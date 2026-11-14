"""Worker outputs as tool calls (Factor 4). A worker's whole output is
one JSON object, {"tool": name, "args": {...}}, checked here against
the registry before anything acts on it. The registry mirrors the
output contracts in prompts/*.md. Pure: no I/O, no run state.
"""

import json

from .errors import StateError

STR = "string"
TEXT = "string, may be empty"
STR_LIST = "list of strings"
INT = "integer"
BOOL = "boolean"
INT_LIST = "list of integers"
FINDINGS = "list of findings"
FINDING_FIELDS = ("id", "class", "severity", "where", "finding", "owner")
ITEMS = "list of test items"
ITEM_KINDS = ("behavior", "example", "edge", "error")
ITEM_FIELDS = {"text": STR, "kind": ITEM_KINDS, "source": STR, "needed": TEXT,
               "criteria": INT_LIST}

ESCALATE = {"escalate": {"question": STR}}

# Keyed by kind, the part of a role the core knows how to enforce. A
# project names its own roles, but each maps to one of these kinds.
TOOLS = {
    "analyzer": {
        "test_list_ready": {"items": ITEMS, "gaps": STR_LIST, "directives": STR_LIST},
        **ESCALATE,
    },
    "red": {
        "red_test_ready": {"item_id": STR, "test_file": STR, "test_name": STR,
                           "last_assertion_line": INT, "defeats": STR, "add_items": ITEMS},
        "no_cheat_left": {"reason": STR},
        **ESCALATE,
    },
    "green": {
        "green_ready": {"summary": STR},
        **ESCALATE,
    },
    "refactor": {
        "refactor_done": {"summary": STR, "changed": BOOL},
        **ESCALATE,
    },
    "review": {
        "review": {"verdict": ("merge-ready", "blocked", "human-gated", "unverified"),
                   "findings": FINDINGS, "verification": STR},
        **ESCALATE,
    },
}
KINDS = tuple(TOOLS)
ALL_TOOLS = sorted({name for tools in TOOLS.values() for name in tools})


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _check(name, value, kind):
    if isinstance(kind, tuple):
        return None if value in kind else f"{name} must be one of {list(kind)}"
    if kind == STR:
        ok = isinstance(value, str) and value.strip() != ""
        return None if ok else f"{name} must be a non-empty string"
    if kind == TEXT:
        return None if isinstance(value, str) else f"{name} must be a string"
    if kind == STR_LIST:
        ok = isinstance(value, list) and all(isinstance(v, str) for v in value)
        return None if ok else f"{name} must be a {kind}"
    if kind == INT:
        return None if _is_int(value) else f"{name} must be an integer"
    if kind == BOOL:
        return None if isinstance(value, bool) else f"{name} must be true or false"
    if kind == INT_LIST:
        ok = isinstance(value, list) and all(_is_int(v) for v in value)
        return None if ok else f"{name} must be a {kind}"
    if kind == ITEMS:
        return _check_items(name, value)
    ok = isinstance(value, list) and all(isinstance(v, dict) for v in value)
    return None if ok else f"{name} must be a list of objects"


def _check_items(name, value):
    """Every item is an object with exactly the item fields, each of its
    shape: the enum and the required fields are checked here, whether an
    item says anything useful is the caller's check."""
    if not isinstance(value, list):
        return f"{name} must be a {ITEMS}"
    problems = []
    for i, item in enumerate(value):
        where = f"{name}[{i}]"
        if not isinstance(item, dict):
            problems.append(f"{where} must be an object")
            continue
        problems += [f"{where} is missing {k!r}" for k in ITEM_FIELDS if k not in item]
        problems += [f"{where} has unexpected {k!r}" for k in item if k not in ITEM_FIELDS]
        problems += [p for k, shape in ITEM_FIELDS.items() if k in item
                     for p in [_check(f"{where}.{k}", item[k], shape)] if p]
    return "; ".join(problems) or None


def parse(text, kind):
    """Return (tool, args) or raise StateError listing every problem."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise StateError(f"worker output is not valid JSON: {exc}") from exc
    if (not isinstance(data, dict) or set(data) != {"tool", "args"}
            or not isinstance(data["tool"], str) or not isinstance(data["args"], dict)):
        raise StateError('worker output must be exactly {"tool": ..., "args": {...}}')
    tool = data["tool"]
    if tool not in ALL_TOOLS:
        raise StateError(f"unknown tool {tool!r}; tools are {ALL_TOOLS}")
    spec = TOOLS[kind].get(tool)
    if spec is None:
        raise StateError(f"tool {tool!r} is not allowed for a {kind} role; "
                         f"it may call {sorted(TOOLS[kind])}")
    args = data["args"]
    problems = [f"missing arg {k!r}" for k in spec if k not in args]
    problems += [f"unexpected arg {k!r}" for k in args if k not in spec]
    problems += [p for k, shape in spec.items() if k in args
                 for p in [_check(k, args[k], shape)] if p]
    if problems:
        raise StateError(f"invalid {tool!r} call: " + "; ".join(problems))
    return tool, args


def schema_for(kind):
    """The tools a role of this kind may call, for its context window."""
    return [
        {"name": name, "args": {k: _describe(v) for k, v in spec.items()}}
        for name, spec in TOOLS[kind].items()
    ]


def _describe(shape):
    if isinstance(shape, tuple):
        return list(shape)
    if shape == ITEMS:
        return {ITEMS: {k: _describe(v) for k, v in ITEM_FIELDS.items()}}
    return shape



def _arg_schema(shape):
    if isinstance(shape, tuple):
        return {"type": "string", "enum": list(shape)}
    if shape == STR:
        return {"type": "string", "minLength": 1}
    if shape == TEXT:
        return {"type": "string"}
    if shape == INT:
        return {"type": "integer"}
    if shape == BOOL:
        return {"type": "boolean"}
    if shape == INT_LIST:
        return {"type": "array", "items": {"type": "integer"}}
    if shape == ITEMS:
        item = {"type": "object", "additionalProperties": False,
                "required": list(ITEM_FIELDS),
                "properties": {k: _arg_schema(v) for k, v in ITEM_FIELDS.items()}}
        return {"type": "array", "items": item}
    if shape == FINDINGS:
        finding = {"type": "object", "additionalProperties": False,
                   "required": list(FINDING_FIELDS),
                   "properties": {f: {"type": "string"} for f in FINDING_FIELDS}}
        return {"type": "array", "items": finding}
    return {"type": "array", "items": {"type": "string"}}


def json_schema(kind):
    """A JSON Schema for one tool call of this kind, for model CLIs that
    enforce a schema while the model writes. Agent CLIs refuse a schema
    whose root is not an object, so `tool` and `args` are not tied to
    each other here; parse() still checks the pairing."""
    return {
        "type": "object", "additionalProperties": False, "required": ["tool", "args"],
        "properties": {
            "tool": {"type": "string", "enum": sorted(TOOLS[kind])},
            "args": {"anyOf": [
                {"type": "object", "additionalProperties": False, "required": list(spec),
                 "properties": {k: _arg_schema(v) for k, v in spec.items()}}
                for spec in TOOLS[kind].values()
            ]},
        },
    }


def host_tools(kind):
    """One tool definition per tool call of this kind, for agent hosts
    that register the factory's tools as real tools (omp RPC host tools):
    the model then calls `implementation_ready` itself, with its
    arguments checked against this schema while it writes."""
    return [
        {"name": name, "label": name,
         "description": f"Finish your {kind} step with `{name}`. Call it once, last.",
         "parameters": {"type": "object", "additionalProperties": False,
                        "required": list(spec),
                        "properties": {k: _arg_schema(v) for k, v in spec.items()}}}
        for name, spec in TOOLS[kind].items()
    ]
