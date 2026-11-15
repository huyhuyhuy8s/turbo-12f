import concurrent.futures
import difflib
import itertools
import json
import os
import subprocess
import tempfile
import unittest

from t12 import model

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BIN = os.path.join(ROOT, "bin", "t12")

TEST_COMMAND = ["python3", "-m", "unittest", "discover", "-s", "tests", "-q"]
OLD_TEST = ("import unittest\n\n\nclass Old(unittest.TestCase):\n"
            "    def test_old(self):\n        self.assertTrue(True)\n")
CRITERIA = ["fa returns 1", "fb returns 1", "fc returns 1"]


def module_test(name):
    return (f"import unittest\nfrom {name} import f{name}\n\n"
            f"class Test{name.upper()}(unittest.TestCase):\n"
            f"    def test_{name}(self):\n        self.assertEqual(f{name}(), 1)\n")


TEST_LAST_LINE = 6


def make_green(name):
    return (f"diff --git a/{name}.py b/{name}.py\n--- a/{name}.py\n+++ b/{name}.py\n"
            f"@@ -1,2 +1,2 @@\n def f{name}():\n-    return 0\n+    return 1\n")


def diff(path, old, new):
    body = difflib.unified_diff(old.splitlines(True), new.splitlines(True),
                                f"a/{path}", f"b/{path}")
    return f"diff --git a/{path} b/{path}\n" + "".join(body)


def unit(unit_id, name, after=(), needed="owns its own module"):
    return {"id": unit_id, "owns": [f"{name}.py"], "tests": [f"tests/test_{name}.py"],
            "after": list(after), "needed": needed}


def item(unit_id, criterion, text=None):
    return {"text": text or f"f{criterion} returns 1", "kind": "behavior", "source": "criterion",
            "needed": "a constant", "criteria": [criterion], "unit": unit_id}


UNITS = [unit("U1", "a"), unit("U2", "b"), unit("U3", "c", after=("U1", "U2"))]
ITEMS = [item("U1", 0), item("U2", 1), item("U3", 2)]
TWO_ITEMS = [item("U1", 0), item("U2", 1), item("U1", 2, "fc returns 1")]
PAIR_UNITS = [UNITS[0], UNITS[1]]
PAIR_ITEMS = [{**item("U1", 0), "criteria": [0, 2]}, item("U2", 1)]
BASE_B = "def fb():\n    return 0\n"
TEST_A_BY_MODULE = ("import unittest\nimport a\n\nclass TestA(unittest.TestCase):\n"
                    "    def test_a(self):\n        self.assertEqual(a.fa(), 1)\n")


class LaneCase(unittest.TestCase):
    task_fields = {}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = os.path.join(self.tmp.name, "product")
        self.home = os.path.join(self.tmp.name, "state")
        os.makedirs(os.path.join(self.repo, "tests"))
        files = {"a.py": "def fa():\n    return 0\n", "b.py": "def fb():\n    return 0\n",
                 "c.py": "def fc():\n    return 0\n", "tests/__init__.py": "",
                 "tests/test_old.py": OLD_TEST}
        for rel, text in files.items():
            with open(os.path.join(self.repo, rel), "w") as fh:
                fh.write(text)
        for args in (("init", "-b", "main"), ("config", "user.email", "t@example.com"),
                     ("config", "user.name", "T"), ("add", "."), ("commit", "-qm", "init")):
            self.git(self.repo, *args)
        task = {"id": "demo-1", "title": "Three modules", "acceptance_criteria": CRITERIA,
                "base": "main", "time_limit_s": 600, "op_limit": 200, "repair_cap": 2,
                "attempt_limit": 20, "test_command": TEST_COMMAND,
                "touches": ["a.py", "b.py", "c.py", "pkg", "tests"],
                "gates": [{"name": "unit", "command": TEST_COMMAND}],
                **self.task_fields}
        self.task_path = os.path.join(self.tmp.name, "task.json")
        with open(self.task_path, "w") as fh:
            json.dump(task, fh)
        self.t12("init", "--repo", self.repo, "--home", self.home)
        self.setup_home()
        self.t12("prepare", "--home", self.home, "--task", self.task_path)
        self.counter = itertools.count(1)

    def tearDown(self):
        self.tmp.cleanup()

    def setup_home(self):
        """Called between `init` and `prepare`, for a case that needs a file in the home."""

    def git(self, cwd, *args):
        result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def t12(self, *args, expect=0):
        result = subprocess.run([BIN, *args], capture_output=True, text=True, timeout=120,
                                env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
        self.assertEqual(result.returncode, expect,
                         f"t12 {' '.join(args)} -> {result.returncode}: "
                         f"{result.stdout} {result.stderr}")
        return result

    def act(self, role, tool, args, unit=None, patch=None, expect=0):
        path = os.path.join(self.tmp.name, f"act-{next(self.counter)}.json")
        with open(path, "w") as fh:
            json.dump({"tool": tool, "args": args}, fh)
        argv = ["act", "--home", self.home, "--task-id", "demo-1", "--role", role,
                "--session", f"sess-{role}-{unit or 'run'}", "--input", path]
        if unit:
            argv += ["--unit", unit]
        if patch is not None:
            with open(path + ".patch", "w", newline="") as fh:
                fh.write(patch)
            argv += ["--patch", path + ".patch"]
        return self.t12(*argv, expect=expect)

    def analyze(self, units=UNITS, items=ITEMS, expect=0, **extra):
        args = {"items": items, "gaps": [], "directives": [], **extra}
        if units is not None:
            args["units"] = units
        return self.act("analyzer", "test_list_ready", args, expect=expect)

    def run_record(self):
        with open(os.path.join(self.home, "runs", "demo-1.json")) as fh:
            return json.load(fh)

    def lane(self, unit_id):
        with open(os.path.join(self.home, "runs", "demo-1", "lanes", f"{unit_id}.json")) as fh:
            return json.load(fh)

    def worktree(self, unit_id):
        return os.path.join(self.home, "worktrees", f"demo-1@{unit_id}")

    def events(self):
        with open(os.path.join(self.home, "runs", "demo-1.events.jsonl")) as fh:
            return [json.loads(line) for line in fh if line.strip()]

    def board(self):
        return json.loads(self.t12("board", "--home", self.home, "--json").stdout)

    def red(self, unit_id, name, item_id=None, expect=0, text=None):
        rel = f"tests/test_{name}.py"
        with open(os.path.join(self.worktree(unit_id), rel), "w") as fh:
            fh.write(text or module_test(name))
        return self.act("red", "red_test_ready", {
            "item_id": item_id or f"{unit_id}-T1", "test_file": rel, "test_name": f"test_{name}",
            "last_assertion_line": TEST_LAST_LINE, "defeats": "a constant zero",
            "add_items": []}, unit=unit_id, expect=expect)

    def green(self, unit_id, patch, expect=0):
        return self.act("green", "green_ready", {"summary": "return one"}, unit=unit_id,
                        patch=patch, expect=expect)

    def no_cheat(self, unit_id):
        return self.act("red", "no_cheat_left", {"reason": "every case is covered"}, unit=unit_id)

    def finish(self, unit_id, name, text=None, patch=None):
        self.red(unit_id, name, text=text)
        self.green(unit_id, patch or make_green(name))
        self.no_cheat(unit_id)

    def integrate(self, unit_id, expect=0):
        return self.t12("tdd-step", "--home", self.home, "--task-id", "demo-1", "--unit", unit_id,
                        expect=expect)

    def tdd_step(self, expect=0):
        return self.t12("tdd-step", "--home", self.home, "--task-id", "demo-1", expect=expect)

    def integration_green(self, patch, expect=0):
        return self.act("green", "green_ready", {"summary": "repair the merge"}, patch=patch,
                        expect=expect)

    @property
    def run_worktree(self):
        return os.path.join(self.home, "worktrees", "demo-1")

    def read(self, unit_id, rel):
        base = self.worktree(unit_id) if unit_id else self.run_worktree
        with open(os.path.join(base, rel)) as fh:
            return fh.read()

    def rewrite(self, path, change):
        with open(path) as fh:
            data = json.load(fh)
        change(data)
        with open(path, "w") as fh:
            json.dump(data, fh)

    def run_path(self):
        return os.path.join(self.home, "runs", "demo-1.json")

    def lane_path(self, unit_id):
        return os.path.join(self.home, "runs", "demo-1", "lanes", f"{unit_id}.json")

    def event_details(self, kind):
        return [e["detail"] for e in self.events() if e["type"] == kind]

    def next_of(self, unit_id=None):
        return {r["unit"]: r for r in self.board()}[unit_id]["next"]


class UnitRulesTest(LaneCase):
    def refused(self, check, units=UNITS, items=ITEMS):
        self.analyze(units=units, items=items, expect=1)
        self.assertEqual(self.run_record()["tdd"]["last_error"]["check"], check)
        self.assertFalse(os.path.isdir(os.path.join(self.home, "runs", "demo-1", "lanes")))
        self.assertEqual(self.run_record()["tdd"]["step"], "analyzer")

    def two(self, **second):
        return [UNITS[0], {**unit("U2", "b"), **second}]

    def test_each_rule_refuses_the_list(self):
        owns_b = {"owns": ["b.py"]}
        cases = {
            "an exact path owned twice": ("unit-overlap", self.two(owns=["a.py"])),
            "a directory prefix of another unit's path": (
                "unit-overlap", [{**UNITS[0], "owns": ["pkg/"]},
                                 {**unit("U2", "b"), "owns": ["pkg/inner.py"]}]),
            "a test path shared with another unit's owns": (
                "unit-overlap", self.two(tests=["a.py"])),
            "a glob in owns": ("unit-paths", self.two(owns=["*.py"])),
            "a glob in tests": ("unit-paths", self.two(tests=["tests/test_*.py"])),
            "an absolute path": ("unit-paths", self.two(owns=["/etc/passwd"])),
            "owns outside the task's touches": ("unit-touches", self.two(owns=["d.py"])),
            "a tests path that does not match test_globs": (
                "unit-tests", self.two(tests=["b_check.py"])),
            "a tests path that exists at the base": (
                "unit-tests", self.two(tests=["tests/test_old.py"])),
            "a tests directory holding an existing test": (
                "unit-tests", [{**UNITS[0], "tests": ["tests/"]},
                               {**unit("U2", "b"), "tests": ["pkg/test_b.py"]}]),
            "an unknown unit in after": ("unit-after", self.two(after=["U9"])),
            "a cycle in after": ("unit-after", [{**UNITS[0], "after": ["U2"]},
                                                self.two(after=["U1"])[1]]),
            "a unit after itself": ("unit-after", self.two(after=["U2"])),
            "a second unit with no needed": ("unit-needed", self.two(needed="  ")),
            "a repeated unit id": ("unit-id", [UNITS[0], {**UNITS[0], **owns_b}]),
        }
        for label, (check, units) in cases.items():
            with self.subTest(label):
                self.refused(check, units=units, items=TWO_ITEMS)

    def test_items_must_name_declared_units_and_every_unit_needs_one(self):
        two = self.two()
        self.refused("unit-items", units=two, items=[item("U1", 0), item("U9", 1), item("U1", 2)])
        self.refused("unit-items", units=two, items=[item("U1", 0), item("U1", 1), item("U1", 2)])
        no_unit = {k: v for k, v in item("U2", 1).items() if k != "unit"}
        self.refused("unit-items", units=two, items=[item("U1", 0), no_unit, item("U1", 2)])

    def test_a_refused_list_is_on_record_as_tdd_list_failed(self):
        self.analyze(units=self.two(owns=["a.py"]), items=TWO_ITEMS, expect=1)
        failed = [e for e in self.events() if e["type"] == "tdd-list-failed"]
        self.assertEqual(failed[-1]["detail"]["check"], "unit-overlap")

    def test_the_first_unit_needs_no_needed(self):
        self.analyze(units=[{**UNITS[0], "needed": ""}, UNITS[1]], items=TWO_ITEMS)
        self.assertEqual(self.run_record()["tdd"]["step"], "lanes")


class FanOutTest(LaneCase):
    def test_fan_out_makes_two_lanes_and_a_waiting_third(self):
        self.analyze()
        run = self.run_record()
        self.assertEqual((run["phase"], run["tdd"]["step"]), ("tdd", "lanes"))
        self.assertEqual(run["version"], 3)
        self.assertEqual([r["unit"] for r in run["lanes"]], ["U1", "U2", "U3"])
        self.assertEqual([r["status"] for r in run["lanes"]], ["active", "active", "waiting"])
        trunk_tip = run["tdd"]["last_good_commit"]
        for unit_id in ("U1", "U2"):
            lane = self.lane(unit_id)
            wt = self.worktree(unit_id)
            self.assertEqual(self.git(wt, "branch", "--show-current"), f"t12/demo-1/{unit_id}")
            self.assertEqual(self.git(wt, "rev-parse", "HEAD"), trunk_tip)
            self.assertEqual((lane["status"], lane["fork_commit"], lane["step"]),
                             ("active", trunk_tip, "red"))
        self.assertNotEqual(self.worktree("U1"), self.worktree("U2"))
        waiting = self.lane("U3")
        self.assertEqual((waiting["status"], waiting["after"], waiting["fork_commit"]),
                         ("waiting", ["U1", "U2"], None))
        self.assertFalse(os.path.exists(self.worktree("U3")))
        self.assertEqual(self.git(self.repo, "branch", "--list", "t12/demo-1/*").count("t12/"), 2)
        self.assertEqual([i["id"] for i in self.lane("U3")["items"]], ["U3-T1"])
        self.assertEqual([i["id"] for i in self.lane("U1")["items"]], ["U1-T1"])
        self.assertEqual(run["tdd"]["items"], [])

    def test_fan_out_events(self):
        self.analyze()
        types = [e["type"] for e in self.events()]
        self.assertIn("tdd-list", types)
        fanout = [e for e in self.events() if e["type"] == "tdd-fanout"][0]["detail"]
        self.assertEqual([(u["unit"], u["status"]) for u in fanout["units"]],
                         [("U1", "active"), ("U2", "active"), ("U3", "waiting")])
        self.assertEqual(model.verify_chain(self.home, self.run_record()), [])

    def test_board_has_a_row_per_lane(self):
        self.analyze()
        rows = self.board()
        run_row = [r for r in rows if r["unit"] is None][0]
        self.assertEqual((run_row["next"], run_row["max_lanes"]), ("lanes: in flight", 3))
        lanes = {r["unit"]: r for r in rows if r["unit"]}
        self.assertEqual(sorted(lanes), ["U1", "U2", "U3"])
        self.assertEqual(lanes["U1"]["next"], "red: red")
        self.assertEqual(lanes["U3"]["next"], "waiting: after U1, U2")
        self.assertEqual(lanes["U3"]["waiting"], "after U1, U2")
        self.assertIsNone(lanes["U1"]["waiting"])
        self.assertEqual({r["phase"] for r in rows}, {"tdd"})

    def test_a_single_unit_list_runs_the_serial_path(self):
        self.analyze(units=[UNITS[0]], items=[item("U1", 0), item("U1", 1), item("U1", 2)])
        run = self.run_record()
        self.assertEqual((run["tdd"]["step"], run.get("lanes")), ("red", None))
        self.assertEqual([i["id"] for i in run["tdd"]["items"]], ["T1", "T2", "T3"])
        self.assertFalse(os.path.exists(os.path.join(self.home, "runs", "demo-1")))
        self.assertEqual([r["unit"] for r in self.board()], [None])

    def test_no_units_runs_the_serial_path(self):
        plain = [{k: v for k, v in i.items() if k != "unit"} for i in ITEMS]
        self.analyze(units=None, items=plain)
        self.assertEqual(self.run_record()["tdd"]["step"], "red")
        self.assertEqual(self.run_record()["tdd"]["items"][0]["id"], "T1")

    def test_an_item_naming_a_unit_nobody_declared_is_refused(self):
        self.analyze(units=None, items=ITEMS, expect=1)
        self.assertEqual(self.run_record()["tdd"]["last_error"]["check"], "unit-items")


class SerialWhenOneLaneTest(LaneCase):
    task_fields = {"max_lanes": 1}

    def test_max_lanes_one_ignores_the_units(self):
        self.analyze()
        run = self.run_record()
        self.assertEqual((run["tdd"]["step"], run.get("lanes")), ("red", None))
        self.assertEqual(len(run["tdd"]["items"]), 3)


class LaneRunTest(LaneCase):
    def setUp(self):
        super().setUp()
        self.analyze()

    def test_a_lane_runs_while_a_sibling_holds_its_lock(self):
        with model.lane_lock(self.home, "demo-1", "U2"):
            self.assertTrue(model.is_lane_busy(self.home, "demo-1", "U2"))
            rows = {r["unit"]: r for r in self.board() if r["unit"]}
            self.assertTrue(rows["U2"]["busy"])
            self.assertFalse(rows["U1"]["busy"])
            self.red("U1", "a")
            self.green("U1", make_green("a"))
            self.t12("act", "--home", self.home, "--task-id", "demo-1", "--role", "red",
                     "--session", "x", "--input", os.devnull, "--unit", "U2", expect=1)
        self.assertFalse(model.is_lane_busy(self.home, "demo-1", "U2"))
        lane = self.lane("U1")
        self.assertEqual((lane["items"][0]["status"], lane["cycle"], lane["step"]),
                         ("done", 1, "red"))

    def test_two_lanes_run_at_the_same_time(self):
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            jobs = [pool.submit(self.red, "U1", "a"), pool.submit(self.red, "U2", "b")]
            for job in jobs:
                job.result()
        self.assertEqual((self.lane("U1")["step"], self.lane("U2")["step"]), ("green", "green"))
        self.assertEqual(model.verify_chain(self.home, self.run_record()), [])

    def test_lanes_do_not_share_commits_or_the_run_worktree(self):
        self.red("U1", "a")
        self.green("U1", make_green("a"))
        self.assertNotEqual(self.git(self.worktree("U1"), "rev-parse", "HEAD"),
                            self.lane("U2")["fork_commit"])
        self.assertEqual(self.git(self.worktree("U2"), "rev-parse", "HEAD"),
                         self.lane("U2")["fork_commit"])
        self.assertFalse(os.path.exists(os.path.join(self.worktree("U2"), "tests", "test_a.py")))
        run_wt = os.path.join(self.home, "worktrees", "demo-1")
        self.assertEqual(self.git(run_wt, "rev-parse", "HEAD"), self.run_record()["base_commit"])

    def test_lane_ids_evidence_and_events_carry_the_unit(self):
        self.red("U2", "b")
        lane = self.lane("U2")
        self.assertEqual(lane["items"][0]["id"], "U2-T1")
        self.assertTrue(lane["red"]["output"]["name"].startswith("U2/"))
        self.assertTrue(os.path.isfile(lane["red"]["output"]["path"]))
        mine = [e for e in self.events() if e["detail"].get("unit")]
        self.assertEqual({e["detail"]["unit"] for e in mine}, {"U2"})
        self.assertIn("tdd-red", [e["type"] for e in mine])
        self.assertIn("worker-output", [e["type"] for e in mine])
        self.assertEqual(self.run_record()["lanes"][1]["status"], "active")
        self.assertEqual(model.verify_chain(self.home, self.run_record()), [])

    def test_a_green_patch_in_another_units_files_is_refused_as_lane_scope(self):
        self.red("U1", "a")
        self.green("U1", make_green("b"), expect=1)
        lane = self.lane("U1")
        self.assertEqual(lane["last_error"]["check"], "lane-scope")
        self.assertEqual((lane["step"], lane["attempts"]["green"]), ("green", 1))
        self.assertEqual(self.git(self.worktree("U1"), "status", "--porcelain"), "")
        failed = [e for e in self.events() if e["type"] == "tdd-green-failed"]
        self.assertEqual((failed[-1]["detail"]["check"], failed[-1]["detail"]["unit"]),
                         ("lane-scope", "U1"))
        self.green("U1", make_green("a"))
        self.assertEqual(self.lane("U1")["items"][0]["status"], "done")

    def test_red_outside_the_units_tests_is_refused_as_lane_scope(self):
        self.red("U1", "b", expect=1, text=module_test("b"))
        self.assertEqual(self.lane("U1")["last_error"]["check"], "lane-scope")

    def test_red_stub_outside_the_units_owns_is_refused_as_lane_scope(self):
        with open(os.path.join(self.worktree("U1"), "b.py"), "w") as fh:
            fh.write("def fb():\n    return 0  # stub\n")
        self.red("U1", "a", expect=1)
        self.assertEqual(self.lane("U1")["last_error"]["check"], "lane-scope")

    def test_a_lane_reaching_its_final_waits_for_integrate(self):
        self.red("U1", "a")
        self.green("U1", make_green("a"))
        self.act("red", "no_cheat_left", {"reason": "every case is covered"}, unit="U1")
        lane = self.lane("U1")
        self.assertEqual((lane["step"], lane["status"]), ("final", "done"))
        rows = {r["unit"]: r for r in self.board()}
        self.assertEqual(rows["U1"]["next"], "tdd-step: integrate")
        self.assertEqual(rows["U2"]["next"], "red: red")
        self.assertEqual(self.run_record()["lanes"][0]["status"], "done")
        self.act("red", "no_cheat_left", {"reason": "again"}, unit="U1", expect=1)

    def test_a_lane_that_has_not_finished_cannot_integrate(self):
        self.t12("tdd-step", "--home", self.home, "--task-id", "demo-1", "--unit", "U1", expect=1)
        self.red("U1", "a")
        self.t12("tdd-step", "--home", self.home, "--task-id", "demo-1", "--unit", "U1", expect=1)
        self.assertEqual(self.lane("U1")["status"], "active")

    def test_context_for_a_lane_is_the_lanes(self):
        out = self.t12("context", "--home", self.home, "--task-id", "demo-1", "--role", "red",
                       "--phase", "tdd", "--unit", "U2").stdout
        context = json.loads(out.rsplit("--- context (JSON) ---\n", 1)[1])
        self.assertEqual(context["worktree"], self.worktree("U2"))
        self.assertEqual([i["id"] for i in context["items"]], ["U2-T1"])
        self.assertEqual((context["unit"], context["writable"]), ("U2", ["b.py", "tests/test_b.py"]))
        self.assertEqual(context["branch"], "t12/demo-1/U2")
        self.red("U2", "b")
        green = self.t12("context", "--home", self.home, "--task-id", "demo-1", "--role",
                         "green", "--phase", "tdd", "--unit", "U2").stdout
        shown = json.loads(green.rsplit("--- context (JSON) ---\n", 1)[1])
        self.assertEqual((shown["current_item"]["id"], shown["writable"]), ("U2-T1", ["b.py"]))
        self.assertNotIn("test_b", json.dumps(shown["current_item"]))

    def test_a_lane_role_must_be_red_green_or_refactor(self):
        self.t12("context", "--home", self.home, "--task-id", "demo-1", "--role", "analyzer",
                 "--phase", "tdd", "--unit", "U1", expect=1)
        self.t12("tool-schema", "--home", self.home, "--task-id", "demo-1", "--role",
                 "reviewer", "--unit", "U1", expect=1)
        schema = self.t12("tool-schema", "--home", self.home, "--task-id", "demo-1", "--role",
                          "red", "--unit", "U1").stdout
        self.assertIn("red_test_ready", schema)
        self.t12("context", "--home", self.home, "--task-id", "demo-1", "--role", "red",
                 "--phase", "tdd", "--unit", "U9", expect=1)

    def test_status_names_the_lane(self):
        status = json.loads(self.t12("status", "--home", self.home, "--task-id", "demo-1",
                                     "--unit", "U3").stdout)
        self.assertEqual((status["unit"], status["status"], status["next"]),
                         ("U3", "waiting", "waiting: after U1, U2"))
        run = json.loads(self.t12("status", "--home", self.home, "--task-id", "demo-1").stdout)
        self.assertEqual([r["unit"] for r in run["lanes"]], ["U1", "U2", "U3"])

    def test_the_analyzer_cannot_act_in_a_lane(self):
        self.act("analyzer", "test_list_ready", {"items": [], "gaps": [], "directives": []},
                 unit="U1", expect=1)


class LanePauseTest(LaneCase):
    def setUp(self):
        super().setUp()
        self.analyze()

    def test_an_escalating_lane_pauses_alone_and_resumes(self):
        self.act("red", "escalate", {"question": "which module?"}, unit="U1")
        self.assertEqual(self.lane("U1")["status"], "paused")
        self.assertEqual(self.run_record()["phase"], "tdd")
        rows = {r["unit"]: r for r in self.board()}
        self.assertEqual(rows["U1"]["next"], "human: answer 'which module?'")
        self.assertEqual(rows["U2"]["next"], "red: red")
        self.red("U2", "b")
        self.red("U1", "a", expect=1)
        self.t12("resume", "--home", self.home, "--task-id", "demo-1", "--unit", "U1", expect=1)
        self.t12("resume", "--home", self.home, "--task-id", "demo-1", "--unit", "U1",
                 "--answer", "module a")
        lane = self.lane("U1")
        self.assertEqual((lane["status"], lane["escalations"][0]["answer"]), ("active", "module a"))
        self.red("U1", "a")
        self.assertEqual(model.verify_chain(self.home, self.run_record()), [])
        self.assertIn("resumed", [e["type"] for e in self.events()])


class LaneAttemptLimitTest(LaneCase):
    task_fields = {"attempt_limit": 1}

    def test_the_attempt_limit_pauses_only_the_lane(self):
        self.analyze()
        args = {"item_id": "U1-T1", "test_file": "tests/test_a.py", "test_name": "t",
                "last_assertion_line": 6, "defeats": "x", "add_items": []}
        self.act("red", "red_test_ready", args, unit="U1", expect=1)
        paused = self.act("red", "red_test_ready", args, unit="U1")
        self.assertIn("paused", paused.stdout)
        lane = self.lane("U1")
        self.assertEqual((lane["status"], lane["escalations"][0]["kind"]),
                         ("paused", "attempt-limit"))
        self.assertEqual(self.run_record()["phase"], "tdd")
        self.assertEqual(self.run_record()["lanes"][0]["status"], "paused")
        self.red("U2", "b")
        self.t12("resume", "--home", self.home, "--task-id", "demo-1", "--unit", "U1",
                 "--answer", "try again")
        lane = self.lane("U1")
        self.assertEqual((lane["status"], lane["attempts"]["red"]), ("active", 0))


class IntegrateTest(LaneCase):
    def setUp(self):
        super().setUp()
        self.analyze()

    def test_a_clean_merge_records_the_lane_on_the_integration_branch(self):
        self.finish("U1", "a")
        before = self.run_record()["tdd"]["last_good_commit"]
        self.integrate("U1")
        run, lane = self.run_record(), self.lane("U1")
        merged = lane["merged_commit"]
        self.assertEqual((lane["status"], run["lanes"][0]["status"]), ("merged", "merged"))
        self.assertEqual(self.git(self.run_worktree, "rev-parse", "HEAD"), merged)
        self.assertEqual(run["tdd"]["last_good_commit"], merged)
        parents = self.git(self.repo, "rev-list", "--parents", "-n1", merged).split()[1:]
        self.assertEqual(len(parents), 2)
        self.assertEqual(parents[0], before)
        self.assertEqual(self.read(None, "a.py"), "def fa():\n    return 1\n")
        self.assertIn("tests/test_a.py", run["tdd"]["locked"])
        self.assertEqual([(i["id"], i["status"]) for i in run["tdd"]["items"]],
                         [("U1-T1", "done")])
        self.assertEqual((run["tdd"]["cycle"], run["tdd"]["merge_order"], run["tdd"]["integrations"],
                          run["tdd"]["integration_failures"]), (1, ["U1"], 1, []))
        self.assertGreaterEqual(run["budgets"]["ops_used"], lane["ops_used"])
        self.assertEqual(model.lane_ops_used(self.home, "demo-1"), 0)
        self.assertEqual(self.next_of(None), "lanes: in flight")
        detail = self.event_details("tdd-integrated")[0]
        self.assertEqual((detail["unit"], detail["via"], detail["order"]), ("U1", "merge", ["U1"]))
        self.assertEqual(model.verify_chain(self.home, run), [])
        self.t12("verify", "--home", self.home, "--task-id", "demo-1")

    def test_a_waiting_lane_starts_once_every_unit_it_follows_is_merged(self):
        self.finish("U1", "a")
        self.integrate("U1")
        self.assertEqual(self.lane("U3")["status"], "waiting")
        self.assertFalse(os.path.exists(self.worktree("U3")))
        self.finish("U2", "b")
        self.integrate("U2")
        run, lane = self.run_record(), self.lane("U3")
        tip = run["tdd"]["last_good_commit"]
        self.assertEqual((lane["status"], lane["fork_commit"]), ("active", tip))
        self.assertEqual(self.git(self.worktree("U3"), "rev-parse", "HEAD"), tip)
        for rel in ("a.py", "b.py"):
            self.assertEqual(self.read("U3", rel), self.read(None, rel))
            self.assertIn("return 1", self.read("U3", rel))
        self.assertEqual(self.next_of("U3"), "red: red")
        self.assertEqual(run["lanes"][2]["status"], "active")
        self.assertEqual(self.event_details("tdd-integrated")[-1]["started"], ["U3"])

    def test_two_lanes_finishing_together_merge_in_unit_id_order(self):
        self.finish("U2", "b")
        self.finish("U1", "a")
        for unit_id in ("U1", "U2"):
            self.rewrite(self.lane_path(unit_id), lambda lane: lane.update(finished_at=1000.0))
        self.assertEqual((self.next_of("U1"), self.next_of("U2")),
                         ("tdd-step: integrate", "waiting: after U1"))
        refused = self.integrate("U2", expect=1)
        self.assertIn("U1", refused.stderr)
        self.assertEqual(self.lane("U2")["status"], "done")
        self.integrate("U1")
        self.integrate("U2")
        self.assertEqual([d["order"] for d in self.event_details("tdd-integrated")],
                         [["U1"], ["U1", "U2"]])

    def test_the_lane_that_finished_first_merges_first(self):
        self.finish("U2", "b")
        self.finish("U1", "a")
        self.assertEqual((self.next_of("U2"), self.next_of("U1")),
                         ("tdd-step: integrate", "waiting: after U2"))
        self.integrate("U1", expect=1)
        self.integrate("U2")
        self.integrate("U1")
        self.assertEqual(self.run_record()["tdd"]["merge_order"], ["U2", "U1"])

    def test_a_textual_conflict_aborts_the_merge_and_pauses_the_run(self):
        self.finish("U2", "b")
        wt = self.run_worktree
        with open(os.path.join(wt, "b.py"), "w") as fh:
            fh.write("def fb():\n    return 2\n")
        self.git(wt, "commit", "-qam", "an edit the lanes did not see")
        injected = self.git(wt, "rev-parse", "HEAD")
        self.rewrite(self.run_path(), lambda run: run["tdd"].update(last_good_commit=injected))
        message = self.integrate("U2")
        run = self.run_record()
        self.assertIn("paused", message.stdout)
        self.assertEqual(run["phase"], "paused")
        self.assertEqual(run["escalations"][-1]["kind"], "integration-conflict")
        self.assertEqual(self.git(wt, "rev-parse", "HEAD"), injected)
        self.assertEqual(self.git(wt, "status", "--porcelain"), "")
        self.assertFalse(os.path.exists(os.path.join(self.repo, ".git", "worktrees", "demo-1",
                                                     "MERGE_HEAD")))
        conflict = self.event_details("tdd-integration-conflict")[0]
        self.assertEqual((conflict["unit"], conflict["files"]), ("U2", ["b.py"]))
        self.assertEqual([f["route"] for f in run["tdd"]["integration_failures"]], ["conflict"])
        self.assertEqual(self.lane("U2")["status"], "done")
        self.act("green", "green_ready", {"summary": "x"}, patch="", expect=1)

    def test_the_integration_lock_serializes_merges_and_lane_commands_do_not_take_it(self):
        self.finish("U1", "a")
        with model.integration_lock(self.home, "demo-1"):
            refused = self.integrate("U1", expect=1)
            self.assertIn("integrating", refused.stderr)
            self.red("U2", "b")
        self.integrate("U1")


class SemanticConflictTest(LaneCase):
    task_fields = {"refactor_skip_max_lines": 100}
    OWN_B = "from a import fa\n\n\ndef fb():\n    return fa() + 1\n"
    FIXED_B = "from a import fa\n\n\ndef fb():\n    return fa()\n"
    MERGED_B = "import a\n\na.fa = lambda: 2\n\n\ndef fb():\n    return 1\n"

    def setUp(self):
        super().setUp()
        self.analyze(units=PAIR_UNITS, items=PAIR_ITEMS)

    def own_failure(self):
        self.finish("U1", "a")
        self.finish("U2", "b", patch=diff("b.py", BASE_B, self.OWN_B))
        self.integrate("U1")
        self.integrate("U2")

    def merged_failure(self):
        self.finish("U1", "a", text=TEST_A_BY_MODULE)
        self.finish("U2", "b", patch=diff("b.py", BASE_B, self.MERGED_B))
        self.integrate("U1")
        self.integrate("U2")

    def green_context(self, *extra):
        out = self.t12("context", "--home", self.home, "--task-id", "demo-1", "--role", "green",
                       "--phase", "tdd", *extra).stdout
        return json.loads(out.rsplit("--- context (JSON) ---\n", 1)[1])

    def test_only_the_merging_lanes_tests_failing_reopens_that_lane_at_green(self):
        self.own_failure()
        run, lane = self.run_record(), self.lane("U2")
        pre_merge = self.lane("U1")["merged_commit"]
        self.assertEqual((lane["status"], lane["step"]), ("active", "green"))
        self.assertEqual((run["tdd"]["step"], run["tdd"]["last_good_commit"]), ("lanes", pre_merge))
        self.assertEqual(self.git(self.run_worktree, "rev-parse", "HEAD"), pre_merge)
        self.assertEqual(self.git(self.worktree("U2"), "rev-parse", "HEAD"),
                         lane["last_good_commit"])
        self.assertNotEqual(lane["last_good_commit"], pre_merge)
        self.assertEqual(self.read("U2", "a.py"), "def fa():\n    return 1\n")
        self.assertEqual(lane["last_error"]["check"], "tests")
        shown = json.dumps(lane["last_error"]["tail"])
        self.assertIn("AssertionError", shown)
        self.assertNotIn("test_b", shown)
        self.assertEqual(run["budgets"]["repairs_used"], 1)
        failure = run["tdd"]["integration_failures"][0]
        self.assertEqual((failure["unit"], failure["route"], failure["failed"]),
                         ("U2", "own-tests", ["U2"]))
        self.assertEqual((self.next_of("U2"), self.next_of(None)), ("green: green", "lanes: in flight"))
        detail = self.event_details("tdd-integration-failed")[0]
        self.assertEqual((detail["route"], detail["failed_lanes"], detail["rolled_back_to"]),
                         ("own-tests", ["U2"], pre_merge))
        context = self.green_context("--unit", "U2")
        self.assertEqual(context["unit"], "U2")
        self.assertIn("AssertionError", json.dumps(context["failure"]))

    def test_a_reopened_lane_merges_once_its_green_fixes_the_merge(self):
        self.own_failure()
        self.green("U2", diff("b.py", self.OWN_B, self.FIXED_B))
        self.assertEqual((self.lane("U2")["status"], self.lane("U2")["step"]), ("done", "final"))
        self.integrate("U2")
        run = self.run_record()
        self.assertEqual(run["lanes"][1]["status"], "merged")
        self.assertEqual(run["tdd"]["merge_order"], ["U1", "U2"])
        self.assertEqual((run["tdd"]["integrations"], len(run["tdd"]["integration_failures"])), (3, 1))
        self.assertEqual(self.read(None, "b.py"), self.FIXED_B)
        self.assertEqual((run["tdd"]["step"], self.next_of(None)), ("final", "tdd-step"))
        self.tdd_step()
        self.t12("verify", "--home", self.home, "--task-id", "demo-1")

    def test_a_merged_lanes_test_failing_goes_to_the_integration_green(self):
        self.merged_failure()
        run = self.run_record()
        pending = run["tdd"]["pending_merge"]
        self.assertEqual(run["tdd"]["step"], "integration-green")
        self.assertEqual((pending["unit"], pending["units"], pending["owns"], pending["failed"]),
                         ("U2", ["U1", "U2"], ["a.py", "b.py"], ["U1"]))
        self.assertEqual(self.next_of(None), "green: green")
        self.assertNotIn("U2", [r["unit"] for r in self.board()])
        self.assertEqual(self.git(self.run_worktree, "rev-parse", "HEAD"), pending["merge_commit"])
        self.assertEqual(run["tdd"]["last_good_commit"], pending["merge_commit"])
        self.assertEqual(run["budgets"]["repairs_used"], 1)
        failure = run["tdd"]["integration_failures"][0]
        self.assertEqual((failure["route"], failure["failed"]), ("integration-green", ["U1"]))
        context = self.green_context()
        self.assertEqual((context["integration"], context["writable"]), (True, ["a.py", "b.py"]))
        self.assertNotIn("test_a", json.dumps(context))
        self.integrate("U2", expect=1)
        self.integration_green(diff("c.py", "def fc():\n    return 0\n",
                                    "def fc():\n    return 1\n"), expect=1)
        self.assertEqual(self.run_record()["tdd"]["last_error"]["check"], "lane-scope")
        self.integration_green(diff("b.py", self.MERGED_B, "def fb():\n    return 1\n"))
        run = self.run_record()
        merge = run["tdd"]["merges"][-1]
        self.assertEqual((merge["unit"], merge["via"]), ("U2", "integration-green"))
        self.assertEqual(run["tdd"]["last_good_commit"], self.lane("U2")["merged_commit"])
        self.assertEqual(self.git(self.run_worktree, "rev-parse", "HEAD"),
                         run["tdd"]["last_good_commit"])
        self.assertEqual((run["tdd"]["pending_merge"], run["tdd"]["step"], self.next_of(None)),
                         (None, "final", "tdd-step"))
        self.assertTrue(any(e["kind"] == "repair" and e["commits"]["green"]
                            for e in run["tdd"]["history"]))
        self.tdd_step()
        self.assertEqual(self.run_record()["phase"], "gates")
        self.t12("verify", "--home", self.home, "--task-id", "demo-1")

    def test_a_green_refused_for_another_units_file_goes_straight_to_the_integration_green(self):
        self.own_failure()
        outside = diff("a.py", "def fa():\n    return 1\n",
                       "def fa():\n    return 1  # changed from another unit\n")
        refused = self.green("U2", outside, expect=1)
        self.assertIn("integration green", refused.stderr)
        run, lane = self.run_record(), self.lane("U2")
        self.assertEqual(lane["attempts"]["green"], 1)
        self.assertEqual((lane["status"], lane["step"]), ("done", "final"))
        self.assertEqual(run["tdd"]["step"], "integration-green")
        self.assertEqual(run["tdd"]["pending_merge"]["unit"], "U2")
        self.assertEqual(run["budgets"]["repairs_used"], 2)
        failures = run["tdd"]["integration_failures"]
        self.assertEqual([(f["unit"], f["route"]) for f in failures], [("U2", "scope-refused")])
        self.assertEqual(self.next_of(None), "green: green")
        self.assertEqual(len(self.event_details("tdd-integration-rerouted")), 1)
        self.integration_green(diff("b.py", self.OWN_B, self.FIXED_B))
        self.assertEqual(self.run_record()["lanes"][1]["status"], "merged")
        self.assertEqual(self.read(None, "b.py"), self.FIXED_B)

    def test_a_refused_green_that_stays_inside_its_owns_keeps_its_attempts(self):
        self.own_failure()
        self.green("U2", diff("b.py", self.OWN_B, self.OWN_B + "# no change in behavior\n"),
                   expect=1)
        lane = self.lane("U2")
        self.assertEqual((lane["step"], lane["attempts"]["green"]), ("green", 1))
        self.assertEqual(self.run_record()["tdd"]["step"], "lanes")


class IntegrationRepairCapTest(LaneCase):
    task_fields = {"refactor_skip_max_lines": 100, "repair_cap": 0}

    def setUp(self):
        super().setUp()
        self.analyze(units=PAIR_UNITS, items=PAIR_ITEMS)

    def test_past_the_repair_cap_the_run_pauses_as_a_gate_failure_does(self):
        self.finish("U1", "a")
        self.finish("U2", "b", patch=diff("b.py", BASE_B, SemanticConflictTest.OWN_B))
        self.integrate("U1")
        self.integrate("U2")
        run = self.run_record()
        self.assertEqual(run["phase"], "paused")
        self.assertEqual(run["escalations"][-1]["kind"], "repair-cap")
        self.assertEqual((self.lane("U2")["status"], self.lane("U2")["step"]), ("active", "green"))
        self.green("U2", diff("b.py", SemanticConflictTest.OWN_B,
                              SemanticConflictTest.FIXED_B), expect=1)
        self.t12("resume", "--home", self.home, "--task-id", "demo-1", "--answer", "go on",
                 expect=1)
        self.t12("resume", "--home", self.home, "--task-id", "demo-1", "--answer", "go on",
                 "--repair-cap", "1")
        self.assertEqual(self.run_record()["phase"], "tdd")
        self.green("U2", diff("b.py", SemanticConflictTest.OWN_B, SemanticConflictTest.FIXED_B))
        self.integrate("U2")
        self.assertEqual(self.run_record()["lanes"][1]["status"], "merged")


class IntegrationGreenAttemptLimitTest(LaneCase):
    task_fields = {"refactor_skip_max_lines": 100, "attempt_limit": 1}

    def test_an_integration_green_failing_past_the_attempt_limit_pauses_the_run(self):
        self.analyze(units=PAIR_UNITS, items=PAIR_ITEMS)
        merged_b = SemanticConflictTest.MERGED_B
        self.finish("U1", "a", text=TEST_A_BY_MODULE)
        self.finish("U2", "b", patch=diff("b.py", BASE_B, merged_b))
        self.integrate("U1")
        self.integrate("U2")
        still_broken = diff("b.py", merged_b, merged_b + "# still broken\n")
        self.integration_green(still_broken, expect=1)
        self.assertEqual(self.run_record()["tdd"]["attempts"]["green"], 1)
        self.assertEqual(self.run_record()["phase"], "tdd")
        paused = self.integration_green(still_broken)
        self.assertIn("paused", paused.stdout)
        run = self.run_record()
        self.assertEqual((run["phase"], run["escalations"][-1]["kind"]), ("paused", "attempt-limit"))
        self.t12("resume", "--home", self.home, "--task-id", "demo-1", "--answer", "look again")
        self.integration_green(diff("b.py", merged_b, "def fb():\n    return 1\n"))
        self.assertEqual(self.run_record()["lanes"][1]["status"], "merged")


class CrossUnitRefactorTest(LaneCase):
    def merge_all(self):
        self.analyze()
        for unit_id, name in (("U1", "a"), ("U2", "b")):
            self.finish(unit_id, name)
            self.integrate(unit_id)
        self.finish("U3", "c")
        self.integrate("U3")

    def test_lines_over_the_threshold_send_the_merged_run_to_one_refactor(self):
        self.merge_all()
        run = self.run_record()
        self.assertEqual((run["tdd"]["step"], run["tdd"]["lines_since_refactor"]), ("refactor", 6))
        self.assertEqual(self.next_of(None), "refactor: refactor")
        self.act("refactor", "refactor_done", {"summary": "nothing to clean", "changed": False},
                 patch="")
        self.assertEqual((self.run_record()["tdd"]["step"], self.next_of(None)),
                         ("final", "tdd-step"))
        self.tdd_step()
        self.assertEqual(self.run_record()["phase"], "gates")

    def test_a_small_change_without_directives_goes_straight_to_final(self):
        self.analyze(units=PAIR_UNITS, items=PAIR_ITEMS)
        for unit_id, name in (("U1", "a"), ("U2", "b")):
            self.finish(unit_id, name)
            self.integrate(unit_id)
        self.assertEqual((self.run_record()["tdd"]["step"], self.next_of(None)),
                         ("final", "tdd-step"))

    def test_a_pending_directive_sends_the_merged_run_to_the_refactor(self):
        self.analyze(units=PAIR_UNITS, items=PAIR_ITEMS, directives=["R1: rename fa"])
        for unit_id, name in (("U1", "a"), ("U2", "b")):
            self.finish(unit_id, name)
            self.integrate(unit_id)
        self.assertEqual((self.run_record()["tdd"]["step"], self.next_of(None)),
                         ("refactor", "refactor: refactor"))


class EndToEndTest(LaneCase):
    task_fields = {"refactor_skip_max_lines": 100}

    def test_three_units_run_from_fan_out_through_integration_to_final(self):
        self.analyze()
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            jobs = [pool.submit(self.finish, "U1", "a"), pool.submit(self.finish, "U2", "b")]
            for job in jobs:
                job.result()
        self.assertEqual(self.next_of("U3"), "waiting: after U1, U2")
        for unit_id in sorted(("U1", "U2"), key=lambda u: (self.lane(u)["finished_at"], u)):
            self.integrate(unit_id)
        self.assertEqual(self.next_of("U3"), "red: red")
        self.finish("U3", "c")
        self.integrate("U3")
        run = self.run_record()
        self.assertEqual([r["status"] for r in run["lanes"]], ["merged"] * 3)
        self.assertEqual((run["tdd"]["step"], self.next_of(None)), ("final", "tdd-step"))
        self.assertEqual(sorted(i["id"] for i in run["tdd"]["items"] if i["status"] == "done"),
                         ["U1-T1", "U2-T1", "U3-T1"])
        self.assertEqual(run["tdd"]["merge_order"][-1], "U3")
        self.tdd_step()
        run = self.run_record()
        self.assertEqual(run["phase"], "gates")
        for name in ("a", "b", "c"):
            self.assertEqual(self.git(self.repo, "show", f"{run['candidate']['commit']}:{name}.py"),
                             f"def f{name}():\n    return 1")
        self.assertEqual(self.next_of(None), "gate")
        self.assertEqual(model.verify_chain(self.home, run), [])
        verified = self.t12("verify", "--home", self.home, "--task-id", "demo-1").stdout
        self.assertNotIn("FAIL", verified)


class LaneBudgetTest(LaneCase):
    task_fields = {"op_limit": 24}

    def setUp(self):
        super().setUp()
        self.analyze()

    def spend(self, unit_id, count):
        for _ in range(count):
            self.act("red", "red_test_ready", {"item_id": f"{unit_id}-T1"}, unit=unit_id, expect=1)

    def test_the_remaining_limits_are_carved_into_shares_with_a_remainder_for_integration(self):
        fanout = self.event_details("tdd-fanout")[0]
        run = self.run_record()
        left_ops = run["budgets"]["op_limit"] - 1
        left_cycles = run["budgets"]["cycle_limit"]
        shares = [u["budget"] for u in fanout["units"]]
        self.assertEqual([s["op_limit"] for s in shares], [5, 5, 5])
        self.assertEqual(fanout["reserve"]["op_limit"], left_ops - 15)
        self.assertLessEqual(sum(s["op_limit"] for s in shares) + fanout["reserve"]["op_limit"],
                             left_ops)
        self.assertLessEqual(sum(s["cycle_limit"] for s in shares) + fanout["reserve"]["cycle_limit"],
                             left_cycles)
        self.assertEqual(self.lane("U1")["budget"]["op_limit"], 5)
        self.assertAlmostEqual(self.lane("U1")["budget"]["cost_share"], 0.25)

    def test_shares_follow_each_units_item_count(self):
        from t12 import tdd
        self.assertEqual(tdd._share(2, 3, 23, 50)["op_limit"], 11)
        self.assertEqual(tdd._share(1, 3, 23, 50)["op_limit"], 5)
        total = sum(tdd._share(w, 6, 100, 50)["op_limit"] for w in (1, 2, 3))
        self.assertLessEqual(total, 75)

    def test_a_lane_that_spends_its_share_pauses_alone_and_names_its_unit(self):
        self.spend("U1", 4)
        self.assertEqual(self.lane("U1")["status"], "active")
        self.spend("U1", 1)
        lane = self.lane("U1")
        pause = lane["escalations"][-1]
        self.assertEqual((lane["status"], pause["kind"], pause["dimension"]),
                         ("paused", "lane-budget", "ops"))
        self.assertIn("lane U1", pause["question"])
        self.assertEqual(self.run_record()["phase"], "tdd")
        self.assertTrue(self.next_of("U1").startswith("human: answer"))
        self.act("red", "no_cheat_left", {"reason": "x"}, unit="U1", expect=1)
        self.finish("U2", "b")
        self.assertEqual(self.lane("U2")["status"], "done")

    def test_a_siblings_unspent_share_is_granted_to_the_paused_lane_on_merge(self):
        self.spend("U1", 5)
        self.finish("U2", "b")
        used = self.lane("U2")["ops_used"]
        self.assertEqual(used, 3)
        self.integrate("U2")
        run, lane = self.run_record(), self.lane("U1")
        self.assertEqual(run["tdd"]["pool"]["op_limit"], 0)
        grant = self.event_details("tdd-integrated")[-1]["grants"]
        self.assertEqual(grant, [{"unit": "U1", "dimension": "ops", "amount": 1}])
        self.assertEqual((lane["status"], lane["budget"]["op_limit"]), ("active", 6))
        self.assertIn("granted 1 ops", lane["escalations"][-1]["answer"])
        self.assertEqual(self.next_of("U1"), "red: red")
        self.red("U1", "a")
        self.assertEqual(self.lane("U1")["step"], "green")

    def test_the_run_total_never_exceeds_the_serial_caps(self):
        self.spend("U1", 5)
        self.finish("U2", "b")
        self.integrate("U2")
        run = self.run_record()
        accounted = (run["tdd"]["pool"]["op_limit"] + run["tdd"]["reserve"]["op_limit"]
                     + sum(self.lane(u)["budget"]["op_limit"] for u in ("U1", "U3"))
                     + self.lane("U2")["ops_used"])
        self.assertLessEqual(accounted, run["budgets"]["op_limit"] - 1)
        spent = run["budgets"]["ops_used"] + model.lane_ops_used(self.home, "demo-1")
        self.assertLessEqual(spent, run["budgets"]["op_limit"])

    def test_a_lane_that_spends_its_cycles_pauses_and_is_granted_cycles_on_merge(self):
        def cap(lane):
            lane["budget"].update(cycle_limit=0, share={**lane["budget"]["share"], "cycle_limit": 0})
        self.rewrite(self.lane_path("U1"), cap)
        self.red("U1", "a")
        paused = self.green("U1", make_green("a"))
        self.assertIn("paused", paused.stdout)
        pause = self.lane("U1")["escalations"][-1]
        self.assertEqual((self.lane("U1")["status"], pause["kind"], pause["dimension"]),
                         ("paused", "lane-budget", "cycles"))
        self.finish("U2", "b")
        self.integrate("U2")
        lane = self.lane("U1")
        self.assertEqual((lane["status"], lane["budget"]["cycle_limit"]), ("active", 1))
        self.no_cheat("U1")
        self.integrate("U1")
        self.assertEqual(self.lane("U1")["status"], "merged")

    def test_a_human_can_raise_a_lane_share_to_resume_it(self):
        self.spend("U1", 5)
        self.t12("resume", "--home", self.home, "--task-id", "demo-1", "--unit", "U1",
                 "--answer", "keep going", expect=1)
        self.t12("resume", "--home", self.home, "--task-id", "demo-1", "--unit", "U1",
                 "--answer", "keep going", "--op-limit", "8")
        lane = self.lane("U1")
        self.assertEqual((lane["status"], lane["budget"]["op_limit"]), ("active", 8))


class LaneDepsTest(LaneCase):
    task_fields = {"verify_deps": ["deps"]}

    def setUp(self):
        super().setUp()
        os.makedirs(os.path.join(self.run_worktree, "deps"))
        with open(os.path.join(self.run_worktree, "deps", "lib.py"), "w") as fh:
            fh.write("VALUE = 1\n")
        self.analyze()

    def test_a_lane_worktree_links_the_declared_dependencies_without_showing_them(self):
        for unit_id in ("U1", "U2"):
            link = os.path.join(self.worktree(unit_id), "deps")
            self.assertTrue(os.path.islink(link))
            self.assertEqual(os.path.realpath(link),
                             os.path.realpath(os.path.join(self.run_worktree, "deps")))
            self.assertEqual(self.git(self.worktree(unit_id), "status", "--porcelain"), "")
        self.red("U1", "a")
        self.assertEqual(self.lane("U1")["step"], "green")
        self.finish("U2", "b")
        self.integrate("U2")
        self.assertEqual(self.run_record()["lanes"][1]["status"], "merged")

    def test_a_lane_started_after_a_merge_gets_the_links_too(self):
        self.finish("U1", "a")
        self.integrate("U1")
        self.finish("U2", "b")
        self.integrate("U2")
        self.assertTrue(os.path.islink(os.path.join(self.worktree("U3"), "deps")))
        self.assertEqual(self.git(self.worktree("U3"), "status", "--porcelain"), "")


class ReviewFixUnitsTest(LaneCase):
    def to_review_fix(self, **more):
        def change(run):
            run["tdd"].update(mode="review-fix", findings={"ids": ["R1"]})
            run.update(more)
        self.rewrite(self.run_path(), change)

    def test_review_fix_mode_may_declare_units_again(self):
        self.to_review_fix()
        self.analyze(items=[{**i, "text": f"R1: {i['text']}"} for i in ITEMS])
        run = self.run_record()
        self.assertEqual((run["tdd"]["mode"], run["tdd"]["step"]), ("review-fix", "lanes"))
        self.assertEqual([r["unit"] for r in run["lanes"]], ["U1", "U2", "U3"])
        self.assertEqual(self.lane("U1")["status"], "active")

    def test_a_unit_id_an_earlier_list_used_is_refused(self):
        used = {"unit": "U1", "status": "merged", "branch": "t12/demo-1/U1", "fork_commit": None,
                "tip": None, "merged_commit": None}
        self.to_review_fix(lanes=[used])
        self.analyze(items=[{**i, "text": f"R1: {i['text']}"} for i in ITEMS], expect=1)
        self.assertEqual(self.run_record()["tdd"]["last_error"]["check"], "unit-id")


class CalibratedLanesTest(LaneCase):
    def setup_home(self):
        with open(os.path.join(self.home, "limits.json"), "w") as fh:
            json.dump({"max_lanes": 1}, fh)

    def test_a_calibrated_max_lanes_of_one_ignores_the_units(self):
        self.assertEqual(self.run_record()["tdd"]["max_lanes"], 1)
        self.analyze()
        run = self.run_record()
        self.assertEqual((run["tdd"]["step"], run.get("lanes")), ("red", None))
        self.assertEqual(len(run["tdd"]["items"]), 3)


class CalibratedLanesTaskWinsTest(CalibratedLanesTest):
    task_fields = {"max_lanes": 2}

    def test_a_calibrated_max_lanes_of_one_ignores_the_units(self):
        self.assertEqual(self.run_record()["tdd"]["max_lanes"], 2)
        self.analyze()
        self.assertEqual(self.run_record()["tdd"]["step"], "lanes")


if __name__ == "__main__":
    unittest.main()
