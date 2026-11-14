#!/bin/bash
# Smoke: happy path on a disposable repo. No network, no model: the tool
# calls a worker would make are written out and passed to `t12 act`.
set -euo pipefail

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
T12="$(realpath -- "${T12:-$ROOT/bin/t12}")"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

REPO="$TMP/product"
HOME_DIR="$TMP/state"
mkdir -p "$REPO/tests"
cd "$REPO"
git init -b main -q
git config user.email smoke@example.com
git config user.name Smoke
printf 'def greet(name="world"):\n    return "Hello!"\n' > app.py
touch tests/__init__.py
git add . && git commit -qm init

export PYTHONDONTWRITEBYTECODE=1
"$T12" init --repo "$REPO" --home "$HOME_DIR"
"$T12" prepare --home "$HOME_DIR" --task "$ROOT/examples/task.json"

WT="$HOME_DIR/worktrees/demo-1"

# One worker output through `t12 act`; an optional patch is the diff a blind
# worktree produced (green and refactor).
act() {
  local role="$1" session="$2" call="$3" patch="${4:-}"
  printf '%s' "$call" > "$TMP/call.json"
  if [ -n "$patch" ]; then
    "$T12" act --home "$HOME_DIR" --task-id demo-1 --role "$role" --session "$session" \
      --input "$TMP/call.json" --patch "$patch"
  else
    "$T12" act --home "$HOME_DIR" --task-id demo-1 --role "$role" --session "$session" \
      --input "$TMP/call.json"
  fi
}

act analyzer sess-analyzer '{"tool":"test_list_ready","args":{"items":[{"text":"greet returns a greeting for a name and for no name","kind":"behavior","source":"greet() with no argument returns '"'"'Hello, world!'"'"'","needed":"a constant greeting","criteria":[0,1]}],"gaps":[],"directives":[]}}'

cat > "$WT/tests/test_greet.py" <<'EOF'
import unittest
from app import greet

class TestGreet(unittest.TestCase):
    def test_named(self):
        self.assertEqual(greet("Ana"), "Hello, Ana!")

    def test_default(self):
        self.assertEqual(greet(), "Hello, world!")
EOF
act red sess-red '{"tool":"red_test_ready","args":{"item_id":"T1","test_file":"tests/test_greet.py","test_name":"test_default","last_assertion_line":9,"defeats":"a constant greeting","add_items":[]}}'

cat > "$TMP/green.patch" <<'EOF'
diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1,2 +1,2 @@
 def greet(name="world"):
-    return "Hello!"
+    return f"Hello, {name}!"
EOF
act green sess-green '{"tool":"green_ready","args":{"summary":"greet formats the name"}}' "$TMP/green.patch"

# A 2-line green is under refactor_skip_max_lines (5): the core skips refactor.
act red sess-red '{"tool":"no_cheat_left","args":{"reason":"every case is covered"}}'

"$T12" tdd-step --home "$HOME_DIR" --task-id demo-1
"$T12" gate --home "$HOME_DIR" --task-id demo-1

cat > "$TMP/review.md" <<'EOF'
Verdict: approve

## Findings

None. Hypotheses checked against the diff; verification re-run.
EOF
"$T12" review --home "$HOME_DIR" --task-id demo-1 --role reviewer \
  --session sess-reviewer --verdict approve --report "$TMP/review.md"

"$T12" check --home "$HOME_DIR" --task-id demo-1
"$T12" manifest --home "$HOME_DIR" --task-id demo-1
"$T12" verify --home "$HOME_DIR" --task-id demo-1

PHASE="$("$T12" status --home "$HOME_DIR" --task-id demo-1 | python3 -c 'import json,sys; print(json.load(sys.stdin)["phase"])')"
test "$PHASE" = "local-verified"
echo "smoke OK: local-verified"
