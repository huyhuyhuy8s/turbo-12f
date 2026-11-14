"""The deterministic TDD engine against real temp git repos. The fake
runner runs the command on a detached checkout of the commit, as the
verification sandbox does, minus the sandbox."""

import copy
import os
import subprocess
import tempfile
import unittest

from t12 import evidence, gitio, model, tdd
from t12 import runner as command_runner
from t12.errors import EvidenceError, StateError

APP = "def add(a, b):\n    return 0\n"
OLD_TEST = ("import unittest\n\n\nclass Old(unittest.TestCase):\n"
            "    def test_old(self):\n        self.assertTrue(True)\n")
ADD_TEST = ("import unittest\n\nfrom app import add\n\n\nclass AddTest(unittest.TestCase):\n"
            "    def test_add(self):\n        self.assertEqual(add(1, 2), 3)\n")
TEST_COMMAND = ["python3", "-m", "unittest", "discover", "-s", "tests", "-t", ".", "-q"]


def sh(repo, *argv):
    return subprocess.run(argv, cwd=repo, check=True, capture_output=True, text=True).stdout


class FakeRunner:
    def __init__(self, scratch):
        self.scratch = scratch
        self.events = []
        self.commands = []
        self.force_exit = {}
        self.run = None
        self.steps = []

    def run_on_commit(self, run, commit, argv, name):
        self.commands.append((commit, list(argv), name))
        path = os.path.join(self.scratch, "verify")
        with gitio.detached_checkout(run["worktree"], path, commit) as checkout:
            obs = command_runner.run(argv, cwd=checkout, timeout_s=60,
                                     env={"PYTHONDONTWRITEBYTECODE": "1"})
        code = self.force_exit.get(argv[0] if argv[0] != "python3" else "tests", obs["exit_code"])
        ref = {"name": name, "observation": {"exit_code": code, "timed_out": False}}
        return code, obs["stdout"] + obs["stderr"], ref

    def save(self, event_type, detail=None):
        self.events.append((event_type, detail))
        self.steps.append(self.run["tdd"]["step"] if self.run else None)

    def types(self):
        return [t for t, _ in self.events]


class EngineCase(unittest.TestCase):
    attempt_limit = 3
    lint_command = None
    refactor_skip_max_lines = 0

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = os.path.join(self.tmp.name, "wt")
        os.makedirs(os.path.join(self.repo, "tests"))
        for rel, text in (("app.py", APP), ("tests/__init__.py", ""), ("tests/test_old.py", OLD_TEST)):
            with open(os.path.join(self.repo, rel), "w") as fh:
                fh.write(text)
        sh(self.repo, "git", "init", "-q", "-b", "main")
        sh(self.repo, "git", "config", "user.email", "t@example.com")
        sh(self.repo, "git", "config", "user.name", "T")
        sh(self.repo, "git", "add", ".")
        sh(self.repo, "git", "commit", "-q", "-m", "init")
        base = gitio.head(self.repo)
        task = {"test_command": TEST_COMMAND, "test_globs": ["tests/**"],
                "lint_command": self.lint_command,
                "refactor_skip_max_lines": self.refactor_skip_max_lines,
                "failure_count_pattern": r"FAILED \(failures=(\d+)\)"}
        self.run_ = {
            "task_id": "demo-1", "phase": "ready", "worktree": self.repo,
            "base_commit": base, "trunk_commit": base, "protected_paths": [],
            "security_review_paths": [], "acceptance_criteria": ["adds", "adds negatives"],
            "gates": [], "reviews": [], "candidate": None, "escalations": [],
            "budgets": {"attempt_limit": self.attempt_limit, "cycle_limit": 50, "ops_used": 0,
                        "op_limit": 300, "repairs_used": 0, "repair_cap": 3},
            "tdd": model.new_tdd({}, task, base),
        }
        self.tdd = self.run_["tdd"]
        self.runner = FakeRunner(self.tmp.name)
        self.runner.run = self.run_

    def tearDown(self):
        self.tmp.cleanup()

    # -- drivers
    def write(self, rel, text):
        with open(os.path.join(self.repo, rel), "w") as fh:
            fh.write(text)

    def patch_for(self, files):
        for rel, text in files.items():
            self.write(rel, text)
        sh(self.repo, "git", "add", "-N", ".")
        patch = sh(self.repo, "git", "diff")
        sh(self.repo, "git", "reset", "-q", "--hard")
        sh(self.repo, "git", "clean", "-fdq")
        return patch

    def call(self, handler, args, **kw):
        return handler("home", self.run_, "role", "session", args, runner=self.runner, **kw)

    def item(self, text="adds two numbers", criteria=(0, 1)):
        return {"text": text, "kind": "behavior", "source": "criterion", "needed": "add()",
                "criteria": list(criteria)}

    def analyze(self, items=None):
        return self.call(tdd.analyze, {"items": items or [self.item()], "gaps": [], "directives": []})

    def red_args(self, item_id="T1", line=8, **extra):
        return {"item_id": item_id, "test_file": "tests/test_add.py", "test_name": "test_add",
                "last_assertion_line": line, "defeats": "return 0", "add_items": [], **extra}

    def red(self, **kw):
        self.write("tests/test_add.py", kw.pop("text", ADD_TEST))
        return self.call(tdd.red, self.red_args(**kw))

    def refused(self, handler, args, check, **kw):
        with self.assertRaises(StateError):
            self.call(handler, args, **kw)
        self.assertEqual(self.tdd["last_error"]["check"], check)

    def to_green(self):
        self.analyze()
        self.red()

    def to_refactor(self):
        self.to_green()
        self.call(tdd.green, {"summary": "add"},
                  patch=self.patch_for({"app.py": "def add(a, b):\n    return a + b\n"}))


class AnalyzeTest(EngineCase):
    def test_refuses_a_list_that_leaves_a_criterion_uncovered(self):
        self.refused(tdd.analyze, {"items": [self.item(criteria=[0])], "gaps": [], "directives": []},
                     "criteria-coverage")
        self.assertEqual(self.tdd["items"], [])
        self.assertEqual(self.run_["phase"], "ready")
        self.assertEqual(self.tdd["attempts"]["analyzer"], 1)

    def test_accepts_a_covering_list_and_assigns_ids(self):
        self.analyze([self.item(criteria=[0]), self.item("negatives", criteria=[1])])
        self.assertEqual([i["id"] for i in self.tdd["items"]], ["T1", "T2"])
        self.assertEqual(self.tdd["step"], "red")
        self.assertEqual(self.run_["phase"], "tdd")
        self.assertIn("tdd-list", self.runner.types())

    def test_review_fix_requires_every_finding_id_to_be_answered(self):
        self.run_["phase"] = "tdd"
        self.tdd.update(mode="review-fix", findings={"source": "review", "report": None,
                                                    "ids": ["F1", "F2"]})
        args = {"items": [self.item("handles F1 overflow", criteria=[])], "gaps": [], "directives": []}
        self.refused(tdd.analyze, args, "finding-coverage")
        args["directives"] = ["rename helpers (F2)"]
        self.call(tdd.analyze, args)
        self.assertEqual(self.tdd["step"], "red")
        self.assertEqual(self.tdd["directives"], ["rename helpers (F2)"])


class RedTest(EngineCase):
    def setUp(self):
        super().setUp()
        self.analyze()

    def test_a_proper_red_is_committed_and_locks_the_test_blobs(self):
        self.red(**{})
        self.assertEqual(self.tdd["step"], "green")
        self.assertEqual(gitio.head(self.repo), self.tdd["red_commit"])
        self.assertIn("tests/test_add.py", self.tdd["locked"])
        self.assertEqual(self.tdd["items"][0]["status"], "red")
        self.assertEqual(self.tdd["items"][0]["test_name"], "test_add")
        self.assertEqual(self.tdd["history"][0]["kind"], "cycle")
        self.assertIn("tdd-red", self.runner.types())

    def test_a_passing_test_is_a_tautology(self):
        with self.assertRaises(StateError):
            self.red(text=ADD_TEST.replace("add(1, 2), 3", "1, 1"))
        self.assertEqual(self.tdd["last_error"]["check"], "red-tautology")
        self.assertEqual(self.tdd["step"], "red")
        self.assertIn("red-failed", self.runner.types())

    def test_an_error_that_is_no_assertion_is_refused(self):
        with self.assertRaises(StateError):
            self.red(text=ADD_TEST.replace("self.assertEqual(add(1, 2), 3)", "raise ValueError('x')"))
        self.assertEqual(self.tdd["last_error"]["check"], "red-assertion")

    def test_the_failure_must_be_at_the_named_last_assertion(self):
        with self.assertRaises(StateError):
            self.red(line=7)
        self.assertEqual(self.tdd["last_error"]["check"], "red-location")

    def test_python_traceback_location_form_is_recognised(self):
        self.red(line=8)
        self.assertEqual(self.tdd["red"]["last_assertion_line"], 8)

    def test_editing_an_existing_test_is_refused(self):
        self.write("tests/test_old.py", OLD_TEST + "    def test_more(self):\n        self.fail()\n")
        with self.assertRaises(StateError):
            self.red()
        self.assertEqual(self.tdd["last_error"]["check"], "red-existing-test")

    def test_a_stub_over_the_line_limit_is_refused(self):
        self.write("app.py", APP + "".join(f"x{i} = {i}\n" for i in range(20)))
        with self.assertRaises(StateError):
            self.red()
        self.assertEqual(self.tdd["last_error"]["check"], "red-stub")

    def test_a_test_file_must_have_changed(self):
        self.refused(tdd.red, self.red_args(), "red-test-file")

    def test_new_item_id_takes_the_first_added_item(self):
        self.write("tests/test_add.py", ADD_TEST)
        self.call(tdd.red, self.red_args("new", add_items=[
            self.item("add negatives", criteria=[1]), self.item("add zero", criteria=[0])]))
        ids = [i["id"] for i in self.tdd["items"]]
        self.assertEqual(ids, ["T1", "T2", "T3"])
        self.assertEqual(self.tdd["current"], "T2")
        self.assertEqual(self.tdd["items"][1]["status"], "red")
        self.assertEqual(self.tdd["items"][2]["status"], "todo")

    def test_an_unknown_item_is_refused(self):
        self.write("tests/test_add.py", ADD_TEST)
        self.refused(tdd.red, self.red_args("T9"), "red-item")

    def test_no_cheat_left_with_unchecked_items_is_refused(self):
        self.refused(tdd.no_cheat_left, {"reason": "done"}, "no-cheat-left")
        self.assertEqual(self.tdd["step"], "red")

    def test_red_attempts_past_the_limit_pause_the_run(self):
        self.tdd["attempts"]["red"] = self.attempt_limit
        message = self.red(text=ADD_TEST.replace("add(1, 2), 3", "1, 1"))
        self.assertTrue(message.startswith("paused"))
        self.assertEqual(self.run_["phase"], "paused")

    def devil_args(self, text="adds negatives", **extra):
        return self.red_args("new", add_items=[self.item(text, criteria=[1])], **extra)

    def test_a_devil_test_may_not_change_production_code(self):
        self.write("tests/test_neg.py", ADD_TEST.replace("test_add", "test_neg"))
        self.write("app.py", APP + "# cheat stub\n")
        with self.assertRaises(StateError):
            self.call(tdd.red, self.devil_args(test_file="tests/test_neg.py", test_name="test_neg"))
        self.assertEqual(self.tdd["last_error"]["check"], "red-stub")
        self.assertIn("current production code", self.tdd["last_error"]["message"])

    def test_a_devil_test_that_passes_on_the_current_code_points_to_no_cheat_left(self):
        self.write("tests/test_neg.py", ADD_TEST.replace("test_add", "test_neg").replace(
            "add(1, 2), 3", "1, 1"))
        with self.assertRaises(StateError):
            self.call(tdd.red, self.devil_args(test_file="tests/test_neg.py", test_name="test_neg"))
        error = self.tdd["last_error"]
        self.assertEqual(error["check"], "red-tautology")
        self.assertIn("already handles this case", error["message"])
        self.assertIn("no_cheat_left", error["message"])

    def test_a_devil_test_that_fails_unaided_is_accepted(self):
        self.write("tests/test_neg.py", ADD_TEST.replace("test_add", "test_neg"))
        self.call(tdd.red, self.devil_args(test_file="tests/test_neg.py", test_name="test_neg"))
        self.assertEqual(self.tdd["step"], "green")
        self.assertEqual(self.tdd["items"][-1]["text"], "adds negatives")
        self.assertEqual(self.tdd["items"][-1]["status"], "red")

    def test_a_stub_is_still_fine_for_an_unchecked_item(self):
        self.write("app.py", APP + "# stub\n")
        self.red()
        self.assertEqual(self.tdd["step"], "green")

    def test_a_pruned_case_gets_the_clearer_message(self):
        self.tdd["gaps"] = ['redundant: Adds  NEGATIVES (same class as adds two numbers)',
                            "ungrounded: nothing"]
        self.write("tests/test_neg.py", ADD_TEST.replace("test_add", "test_neg").replace(
            "add(1, 2), 3", "1, 1"))
        with self.assertRaises(StateError):
            self.call(tdd.red, self.devil_args(test_file="tests/test_neg.py", test_name="test_neg"))
        message = self.tdd["last_error"]["message"]
        self.assertIn("analyzer pruned this as redundant", message)
        self.assertIn("only if the current code fails it", message)

    def test_the_context_lists_the_pruned_items(self):
        self.assertEqual(tdd.red_context(self.run_)["redundant"], [])
        self.tdd["gaps"] = ['redundant: 6 -> "Fizz" (same class as 3)', "ungrounded: null (x)",
                            "redundant: 10 -> \"Buzz\""]
        self.assertEqual(tdd.red_context(self.run_)["redundant"],
                         ['6 -> "Fizz"', '10 -> "Buzz"'])


class GreenTest(EngineCase):
    def setUp(self):
        super().setUp()
        self.to_green()
        self.red_commit = self.tdd["red_commit"]

    def good(self):
        return self.patch_for({"app.py": "def add(a, b):\n    return a + b\n"})

    def test_a_patch_that_passes_makes_the_item_done_and_moves_to_refactor(self):
        self.call(tdd.green, {"summary": "add"}, patch=self.good())
        self.assertEqual(self.tdd["items"][0]["status"], "done")
        self.assertEqual(self.tdd["step"], "refactor")
        self.assertEqual(gitio.head(self.repo), self.tdd["green_commit"])
        self.assertNotEqual(self.tdd["green_commit"], self.red_commit)
        self.assertTrue(gitio.is_clean(self.repo))
        self.assertEqual([c[2].split("-", 2)[2] for c in self.runner.commands[-1:]], ["green-tests.txt"])

    def test_a_patch_touching_a_test_is_refused_and_leaves_nothing_behind(self):
        patch = self.patch_for({"tests/test_add.py": ADD_TEST + "# edit\n",
                                "app.py": "def add(a, b):\n    return a + b\n"})
        self.refused(tdd.green, {"summary": "x"}, "tests-touched", patch=patch)
        self.assertEqual(gitio.head(self.repo), self.red_commit)
        self.assertTrue(gitio.is_clean(self.repo))
        self.assertEqual(self.tdd["step"], "green")

    def test_a_protected_path_is_refused(self):
        self.run_["protected_paths"] = ["app.py"]
        self.refused(tdd.green, {"summary": "x"}, "protected", patch=self.good())
        self.assertTrue(gitio.is_clean(self.repo))

    def test_an_empty_patch_is_refused(self):
        self.refused(tdd.green, {"summary": "x"}, "empty-patch", patch="")

    def test_failing_tests_roll_back_and_count_an_attempt_with_a_sanitized_error(self):
        patch = self.patch_for({"app.py": "def add(a, b):\n    return a - b\n"})
        self.refused(tdd.green, {"summary": "x"}, "tests", patch=patch)
        self.assertEqual(gitio.head(self.repo), self.red_commit)
        self.assertTrue(gitio.is_clean(self.repo))
        self.assertEqual(self.tdd["attempts"]["green"], 1)
        tail = "\n".join(self.tdd["last_error"]["tail"])
        self.assertIn("AssertionError: -1 != 3", tail)
        self.assertNotIn("test_add", tail)
        self.assertNotIn("tests/", tail)
        self.assertIn("tdd-green-failed", self.runner.types())
        context = tdd.green_context(self.run_)
        self.assertEqual(context["failure"], self.tdd["last_error"]["tail"])
        self.assertNotIn("test_add", repr(context))

    def test_the_attempt_limit_pauses_with_an_escalation(self):
        bad = self.patch_for({"app.py": "def add(a, b):\n    return a - b\n"})
        self.tdd["attempts"]["green"] = self.attempt_limit
        message = self.call(tdd.green, {"summary": "x"}, patch=bad)
        self.assertTrue(message.startswith("paused"))
        self.assertEqual(self.run_["phase"], "paused")
        self.assertEqual(self.run_["escalations"][-1]["kind"], "attempt-limit")
        self.assertIsNone(self.run_["escalations"][-1]["answer"])


class LintTest(EngineCase):
    lint_command = ["python3", "-c", "import sys; sys.exit(1)"]

    def test_a_failing_lint_rolls_green_back(self):
        self.to_green()
        patch = self.patch_for({"app.py": "def add(a, b):\n    return a + b\n"})
        self.refused(tdd.green, {"summary": "x"}, "lint", patch=patch)
        self.assertEqual(gitio.head(self.repo), self.tdd["red_commit"])
        self.assertEqual(self.tdd["attempts"]["green"], 1)


class RefactorTest(EngineCase):
    def setUp(self):
        super().setUp()
        self.to_refactor()
        self.green_commit = self.tdd["green_commit"]

    def test_a_breaking_refactor_is_rolled_back(self):
        patch = self.patch_for({"app.py": "def add(a, b):\n    return 0\n"})
        self.refused(tdd.refactor, {"summary": "x", "changed": True}, "tests", patch=patch)
        self.assertEqual(gitio.head(self.repo), self.green_commit)
        self.assertTrue(gitio.is_clean(self.repo))
        self.assertEqual(self.tdd["attempts"]["refactor"], 1)
        self.assertEqual(self.tdd["last_error"]["tail"],
                         ["red tests or lint failed after your refactor"])
        self.assertEqual(self.tdd["step"], "refactor")

    def test_a_clean_refactor_clears_directives_and_counts_a_cycle(self):
        self.tdd["directives"] = ["inline the helper"]
        patch = self.patch_for({"app.py": "def add(a, b):\n    # sum\n    return a + b\n"})
        self.call(tdd.refactor, {"summary": "x", "changed": True}, patch=patch)
        self.assertEqual(self.tdd["directives"], [])
        self.assertEqual(self.tdd["cycle"], 1)
        self.assertEqual(self.tdd["step"], "red")
        self.assertNotEqual(self.tdd["last_good_commit"], self.green_commit)

    def test_an_empty_patch_with_changed_false_is_fine(self):
        self.call(tdd.refactor, {"summary": "nothing to do", "changed": False}, patch="")
        self.assertEqual(self.tdd["cycle"], 1)
        self.assertEqual(self.tdd["last_good_commit"], self.green_commit)

    def test_changed_true_with_no_patch_is_refused(self):
        self.refused(tdd.refactor, {"summary": "x", "changed": True}, "empty-patch", patch="")

    def test_the_cycle_limit_pauses_the_run(self):
        self.run_["budgets"]["cycle_limit"] = 1
        message = self.call(tdd.refactor, {"summary": "x", "changed": False}, patch="")
        self.assertTrue(message.startswith("paused"))
        self.assertEqual(self.run_["escalations"][-1]["kind"], "cycle-limit")

    def test_the_context_lists_production_files_only(self):
        context = tdd.refactor_context(self.run_)
        self.assertEqual(context["changed_files"], ["app.py"])
        self.assertIn("app.py", context["diff_stat"])
        self.assertNotIn("test_add", context["diff_stat"])


class RefactorSkipTest(EngineCase):
    refactor_skip_max_lines = 5
    SECOND = ADD_TEST.replace("add(1, 2), 3", "add(2, 2), 4").replace("test_add", "test_add2")

    def setUp(self):
        super().setUp()
        self.analyze([self.item("adds ones", criteria=[0]), self.item("adds twos", criteria=[1])])

    def green(self, body):
        return self.call(tdd.green, {"summary": "x"},
                         patch=self.patch_for({"app.py": f"def add(a, b):\n    return {body}\n"}))

    def second_red(self):
        self.write("tests/test_add2.py", self.SECOND)
        self.call(tdd.red, self.red_args("T2", test_file="tests/test_add2.py",
                                         test_name="test_add2"))

    def test_a_small_green_skips_the_refactor_and_counts_the_cycle(self):
        self.red()
        message = self.green("3")
        self.assertIn("refactor skipped", message)
        self.assertEqual((self.tdd["step"], self.tdd["cycle"]), ("red", 1))
        self.assertEqual(self.tdd["lines_since_refactor"], 2)
        self.assertEqual(self.tdd["last_item"], "T1")
        self.assertIsNone(self.tdd["current"])
        self.assertFalse(self.tdd["history"][0]["open"])
        self.assertNotIn("tdd-refactor", self.runner.types())
        self.assertEqual(self.runner.events[-1], (
            "tdd-refactor-skipped", {"item": "T1", "lines_since_refactor": 2, "limit": 5}))
        self.assertEqual(model.tdd_summary(self.run_)["cycles"], 1)

    def test_small_greens_accumulate_until_a_refactor_runs_and_resets_the_debt(self):
        self.tdd["refactor_skip_max_lines"] = 3
        self.red()
        self.green("3")
        self.assertEqual(self.tdd["step"], "red")
        self.second_red()
        self.green("a + b")
        self.assertEqual(self.tdd["lines_since_refactor"], 4)
        self.assertEqual((self.tdd["step"], self.tdd["cycle"]), ("refactor", 1))
        self.assertEqual(self.runner.types().count("tdd-refactor-skipped"), 1)
        self.call(tdd.refactor, {"summary": "none", "changed": False}, patch="")
        self.assertEqual(self.tdd["lines_since_refactor"], 0)
        self.assertEqual((self.tdd["step"], self.tdd["cycle"]), ("red", 2))

    def test_the_step_is_set_before_the_last_save_persists_the_run(self):
        self.red()
        self.green("3")
        self.assertEqual(self.runner.steps[-1], "red")
        self.tdd["refactor_skip_max_lines"] = 0
        self.second_red()
        self.green("a + b")
        self.assertEqual(self.runner.steps[-1], "refactor")

    def test_pending_directives_force_the_refactor(self):
        self.tdd["directives"] = ["inline the helper"]
        self.red()
        self.green("3")
        self.assertEqual(self.tdd["step"], "refactor")
        self.assertEqual(self.tdd["cycle"], 0)
        self.assertNotIn("tdd-refactor-skipped", self.runner.types())

    def test_a_limit_of_zero_never_skips(self):
        self.tdd["refactor_skip_max_lines"] = 0
        self.red()
        self.green("3")
        self.assertEqual(self.tdd["step"], "refactor")

    def test_a_skip_still_honours_the_cycle_limit(self):
        self.run_["budgets"]["cycle_limit"] = 1
        self.red()
        message = self.green("3")
        self.assertTrue(message.startswith("paused"))
        self.assertEqual(self.run_["escalations"][-1]["kind"], "cycle-limit")


class FinalTest(EngineCase):
    def finish_cycle(self):
        self.to_refactor()
        self.call(tdd.refactor, {"summary": "none", "changed": False}, patch="")

    def test_final_pins_the_candidate_and_moves_to_gates(self):
        self.finish_cycle()
        self.assertEqual(self.tdd["step"], "red")
        self.call(tdd.no_cheat_left, {"reason": "nothing left"})
        self.assertEqual(self.tdd["step"], "final")
        tdd.final("home", self.run_, runner=self.runner)
        self.assertEqual(self.run_["phase"], "gates")
        self.assertEqual(self.run_["candidate"]["commit"], gitio.head(self.repo))
        self.assertEqual(self.tdd["final"]["commit"], gitio.head(self.repo))
        self.assertEqual(self.tdd["step"], "done")
        self.assertEqual(self.runner.types()[-1], "tdd-final")

    def test_a_failing_final_goes_back_to_green_with_findings(self):
        self.finish_cycle()
        self.call(tdd.no_cheat_left, {"reason": "nothing left"})
        self.runner.force_exit["tests"] = 1
        with self.assertRaises(StateError):
            tdd.final("home", self.run_, runner=self.runner)
        self.assertEqual(self.tdd["step"], "green")
        self.assertEqual(self.run_["phase"], "tdd")
        self.assertIsNone(self.run_["candidate"])
        self.assertIn("tdd-final-failed", self.runner.types())

    def test_final_needs_the_final_step(self):
        self.to_green()
        with self.assertRaises(StateError):
            tdd.final("home", self.run_, runner=self.runner)

    def test_final_refuses_tests_that_moved_since_red(self):
        self.finish_cycle()
        self.call(tdd.no_cheat_left, {"reason": "nothing left"})
        self.tdd["locked"]["tests/test_add.py"] = "0" * 40
        with self.assertRaises(StateError):
            tdd.final("home", self.run_, runner=self.runner)
        self.assertEqual(self.tdd["last_error"]["check"], "tests-touched")


class SanitizeTest(unittest.TestCase):
    OUTPUT = "\n".join([
        "FAIL: test_add (tests.test_add.AddTest.test_add)",
        "----------------------------------------------------------------------",
        "Traceback (most recent call last):",
        '  File "/w/tests/test_add.py", line 8, in test_add',
        "    self.assertEqual(add(1, 2), 3)",
        "AssertionError: 0 != 3",
        "",
        "Ran 1 test in 0.001s",
        "FAILED (failures=1)",
    ])

    def test_keeps_the_assertion_line_and_drops_test_paths_and_names(self):
        lines = tdd.sanitize_failure(self.OUTPUT, ["tests/**"], ["test_add"], "AssertionError")
        self.assertEqual(lines, ["AssertionError: 0 != 3"])

    def test_keeps_the_line_after_an_assertion_unless_it_names_a_test(self):
        output = "AssertionError: 1 != 2\nactual: 1\nAssertionError: 3 != 4\nsee tests/test_x.py:3\n"
        lines = tdd.sanitize_failure(output, ["tests/**"], [], "AssertionError")
        self.assertEqual(lines, ["AssertionError: 1 != 2", "actual: 1", "AssertionError: 3 != 4"])

    def test_dotted_module_paths_count_as_test_paths(self):
        lines = tdd.sanitize_failure("AssertionError: x\ntests.test_add.AddTest failed\n",
                                     ["tests/**"], [], "AssertionError")
        self.assertEqual(lines, ["AssertionError: x"])


class VerifyCheckTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def ref(self, name, code):
        obs = {"argv": ["t"], "exit_code": code, "timed_out": False, "duration_s": 0.1,
               "stdout": "out\n", "stderr": ""}
        return evidence.write_observation(self.home, "demo-1", name, obs)

    def run_record(self, red_code=1):
        final = {"commit": "c" * 40, "tests": self.ref("final.txt", 0), "lint": None}
        entry = {"kind": "cycle", "cycle": 1, "red": self.ref("red.txt", red_code),
                 "green_tests": self.ref("green.txt", 0), "green_lint": None,
                 "refactor_tests": None, "refactor_lint": None}
        return {"task_id": "demo-1", "phase": "review", "gates": [],
                "candidate": {"commit": "c" * 40},
                "tdd": {"final": final, "history": [entry], "lint_command": None}}

    def test_a_consistent_chain_verifies(self):
        self.assertTrue(evidence.verify_check(self.home, self.run_record()))

    def test_a_red_that_passed_is_refused(self):
        with self.assertRaises(EvidenceError):
            evidence.verify_check(self.home, self.run_record(red_code=0))

    def test_a_final_run_on_another_commit_is_refused(self):
        run = self.run_record()
        run["tdd"]["final"]["commit"] = "d" * 40
        with self.assertRaises(EvidenceError):
            evidence.verify_check(self.home, run)

    def test_a_changed_artifact_is_refused(self):
        run = self.run_record()
        with open(run["tdd"]["final"]["tests"]["path"], "a") as fh:
            fh.write("tampered")
        with self.assertRaises(EvidenceError):
            evidence.verify_check(self.home, run)


class LaneRunner(FakeRunner):
    """The fake runner for a lane: commands run on the lane's worktree."""

    lane = None

    def run_on_commit(self, run, commit, argv, name):
        return super().run_on_commit(dict(run, worktree=self.lane["worktree"]), commit, argv, name)


class LaneEngineCase(EngineCase):
    """The engine on one lane of a parallel run: the lane record and worktree,
    never the run's own."""

    def setUp(self):
        super().setUp()
        self.run_["phase"] = "tdd"
        self.run_["task_id"] = "demo-1"
        self.tdd["step"] = "lanes"
        home = os.path.join(self.tmp.name, "state")
        os.makedirs(os.path.join(home, "worktrees"))
        spec = {"id": "U1", "owns": ["app.py"], "tests": ["tests/test_add.py"], "after": [],
                "needed": ""}
        self.lane = model.new_lane(home, self.run_, spec, "active")
        model.new_item(self.lane, self.item())
        tdd.start_lane(home, self.run_, self.lane)
        self.runner = LaneRunner(self.tmp.name)
        self.runner.run = self.run_
        self.runner.lane = self.lane
        self.wt = self.lane["worktree"]

    def lane_call(self, handler, args, **kw):
        return self.call(handler, args, lane=self.lane, **kw)

    def lane_refused(self, handler, args, check, **kw):
        with self.assertRaises(StateError):
            self.lane_call(handler, args, **kw)
        self.assertEqual(self.lane["last_error"]["check"], check)

    def lane_red(self, rel="tests/test_add.py", text=ADD_TEST, **kw):
        with open(os.path.join(self.wt, rel), "w") as fh:
            fh.write(text)
        return self.lane_call(tdd.red, self.red_args("U1-T1", test_file=rel), **kw)

    def lane_patch(self, files):
        for rel, text in files.items():
            with open(os.path.join(self.wt, rel), "w") as fh:
                fh.write(text)
        sh(self.wt, "git", "add", "-N", ".")
        patch = sh(self.wt, "git", "diff")
        sh(self.wt, "git", "reset", "-q", "--hard")
        sh(self.wt, "git", "clean", "-fdq")
        return patch


class LaneEngineTest(LaneEngineCase):
    def test_a_lane_cycle_changes_only_the_lane(self):
        before = copy.deepcopy(self.run_)
        self.lane_red()
        self.lane_call(tdd.green, {"summary": "add"},
                       patch=self.lane_patch({"app.py": "def add(a, b):\n    return a + b\n"}))
        self.lane_call(tdd.refactor, {"summary": "nothing", "changed": False}, patch="")
        self.assertEqual(self.run_, before)
        self.assertEqual(gitio.head(self.repo), before["tdd"]["last_good_commit"])
        self.assertEqual(gitio.head(self.wt), self.lane["last_good_commit"])
        self.assertEqual(self.lane["items"][0]["status"], "done")
        self.assertEqual((self.lane["cycle"], self.lane["step"]), (1, "red"))

    def test_a_lane_names_its_evidence_and_counts_its_own_operations(self):
        self.lane_red()
        self.assertEqual(self.lane["red"]["output"]["name"], "U1/tdd-001-red.txt")
        self.assertEqual((self.lane["ops_used"], self.run_["budgets"]["ops_used"]), (1, 0))

    def test_red_outside_the_units_tests_is_lane_scope(self):
        self.lane["tests"] = ["tests/test_other.py"]
        self.lane["last_good_commit"] = gitio.head(self.wt)
        self.write_lane("tests/test_add.py", ADD_TEST)
        self.lane_refused(tdd.red, self.red_args("U1-T1"), "lane-scope")

    def write_lane(self, rel, text):
        with open(os.path.join(self.wt, rel), "w") as fh:
            fh.write(text)

    def test_a_green_patch_outside_the_units_owns_is_lane_scope(self):
        self.lane_red()
        self.lane["owns"] = ["other.py"]
        patch = self.lane_patch({"app.py": "def add(a, b):\n    return a + b\n"})
        self.lane_refused(tdd.green, {"summary": "x"}, "lane-scope", patch=patch)
        self.assertTrue(gitio.is_clean(self.wt))
        self.assertEqual(self.lane["attempts"]["green"], 1)

    def test_a_refactor_patch_outside_the_units_owns_is_lane_scope(self):
        self.lane_red()
        self.lane_call(tdd.green, {"summary": "add"},
                       patch=self.lane_patch({"app.py": "def add(a, b):\n    return a + b\n"}))
        self.assertEqual(self.lane["step"], "refactor")
        self.lane["owns"] = ["other.py"]
        patch = self.lane_patch({"app.py": "def add(a, b):\n    return b + a\n"})
        self.lane_refused(tdd.refactor, {"summary": "x", "changed": True}, "lane-scope",
                          patch=patch)

    def test_no_cheat_left_finishes_the_lane(self):
        self.lane_red()
        self.lane_call(tdd.green, {"summary": "add"},
                       patch=self.lane_patch({"app.py": "def add(a, b):\n    return a + b\n"}))
        self.lane_call(tdd.refactor, {"summary": "nothing", "changed": False}, patch="")
        self.lane_call(tdd.no_cheat_left, {"reason": "done"})
        self.assertEqual((self.lane["step"], self.lane["status"]), ("final", "done"))
        self.assertEqual(self.tdd["step"], "lanes")

    def test_a_lane_that_is_not_active_takes_no_call(self):
        self.lane["status"] = "paused"
        with self.assertRaises(StateError):
            self.lane_red()
        self.lane["status"] = "active"
        self.run_["phase"] = "paused"
        with self.assertRaises(StateError):
            self.lane_red()

    def test_the_analyzer_does_not_run_in_a_lane(self):
        with self.assertRaises(StateError):
            self.lane_call(tdd.analyze, {"items": [self.item()], "gaps": [], "directives": []})

    def test_events_are_saved_through_the_lane_runner(self):
        self.lane_red()
        self.assertEqual(self.runner.types(), ["tdd-red"])

    def test_contexts_are_the_lanes(self):
        context = tdd.red_context(self.run_, self.lane)
        self.assertEqual([i["id"] for i in context["items"]], ["U1-T1"])
        self.assertEqual((context["unit"], context["writable"]), ("U1", ["app.py", "tests/test_add.py"]))
        self.lane_red()
        context = tdd.green_context(self.run_, self.lane)
        self.assertEqual((context["current_item"]["id"], context["writable"]), ("U1-T1", ["app.py"]))
        self.assertNotIn("unit", tdd.green_context(self.run_))


class LaneAttemptLimitTest(LaneEngineCase):
    attempt_limit = 1

    def test_the_limit_pauses_the_lane_and_leaves_the_run_running(self):
        args = self.red_args("U1-T1")
        self.lane_refused(tdd.red, args, "red-test-file")
        message = self.lane_call(tdd.red, args)
        self.assertTrue(message.startswith("paused"))
        self.assertEqual(self.lane["status"], "paused")
        self.assertEqual(self.lane["escalations"][0]["kind"], "attempt-limit")
        self.assertEqual(self.run_["phase"], "tdd")
        self.assertEqual(self.run_["escalations"], [])
        self.assertEqual(self.runner.types()[-1], "human-contact")


class StartLaneTest(LaneEngineCase):
    def test_a_started_lane_forks_from_the_integration_tip(self):
        self.assertEqual(self.lane["fork_commit"], self.tdd["last_good_commit"])
        self.assertEqual(gitio.current_branch(self.wt), "t12/demo-1/U1")
        self.assertEqual(gitio.head(self.wt), self.tdd["last_good_commit"])

    def test_starting_again_replaces_what_a_crashed_step_left(self):
        with open(os.path.join(self.wt, "stray.txt"), "w") as fh:
            fh.write("x")
        tdd.start_lane("home", self.run_, self.lane)
        self.assertFalse(os.path.exists(os.path.join(self.wt, "stray.txt")))


if __name__ == "__main__":
    unittest.main()
