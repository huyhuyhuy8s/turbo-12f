import copy
import json
import os
import subprocess
import tempfile
import time
import unittest

from t12 import model, profile, store
from t12.errors import BudgetError, StateError

EXPECTED = {
    "unadmitted": {"ready"},
    "ready": {"tdd", "paused", "stopped"},
    "tdd": {"gates", "paused", "stopped"},
    "gates": {"tdd", "review", "paused", "stopped"},
    "review": {"tdd", "local-verified", "paused", "stopped"},
    "local-verified": set(),
    "paused": {"ready", "tdd", "gates", "review", "stopped"},
    "stopped": set(),
}

TEST_COMMAND = ["python3", "-m", "unittest", "discover", "-s", "tests", "-q"]


def _bare_run(phase):
    return {
        "version": model.VERSION,
        "task_id": "spec",
        "phase": phase,
        "prior_phase": None,
        "budgets": {
            "time_limit_s": 3600,
            "op_limit": 100,
            "repair_cap": 3,
            "cycle_limit": 50,
            "attempt_limit": 3,
            "started_at": time.time(),
            "ops_used": 0,
            "repairs_used": 0,
        },
    }


def _tdd_run(phase, step="analyzer", **fields):
    run = _bare_run(phase)
    run["gates"] = [{"name": "unit", "status": "pending"}]
    run["tdd"] = model.new_tdd({}, {"test_command": TEST_COMMAND}, "base")
    run["tdd"]["step"] = step
    run["tdd"].update(fields)
    run["required_reviews"] = ["reviewer"]
    run["candidate"] = None
    run["reviews"] = []
    run["escalations"] = []
    return run


class TransitionMatrixTest(unittest.TestCase):
    def test_matrix_matches_spec(self):
        self.assertEqual(set(EXPECTED), set(model.PHASES))
        for phase in model.PHASES:
            for target in model.PHASES:
                run = _bare_run(phase)
                before = copy.deepcopy(run)
                allowed = target in EXPECTED[phase] or target == phase
                if allowed:
                    model.transition(run, target)
                    self.assertEqual(run["phase"], target, f"{phase} -> {target}")
                else:
                    with self.assertRaises(StateError, msg=f"{phase} -> {target}"):
                        model.transition(run, target)
                    self.assertEqual(run, before, f"{phase} -> {target} mutated state")

    def test_every_phase_appears_in_transitions(self):
        self.assertEqual(set(model.TRANSITIONS), set(model.PHASES))
        for phase, targets in model.TRANSITIONS.items():
            self.assertEqual(targets, EXPECTED[phase], phase)

    def test_the_old_lanes_are_gone(self):
        for old in ("red", "green"):
            self.assertNotIn(old, model.PHASES)


class NextStepTest(unittest.TestCase):
    def test_ready_starts_with_the_analyzer(self):
        self.assertEqual(model.next_step(_tdd_run("ready")), "analyzer: analyze")

    def test_every_tdd_step_names_its_actor(self):
        expected = {
            "analyzer": "analyzer: analyze",
            "lanes": "lanes: in flight",
            "red": "red: red",
            "green": "green: green",
            "refactor": "refactor: refactor",
            "integration-green": "green: green",
            "final": "tdd-step",
            "done": "tdd-step",
        }
        self.assertEqual(set(expected), set(model.TDD_STEPS))
        for step, line in expected.items():
            self.assertEqual(model.next_step(_tdd_run("tdd", step)), line, step)

    def test_gates_run_as_a_script_step(self):
        run = _tdd_run("gates")
        self.assertEqual(model.next_step(run), "gate")
        run["gates"][0]["status"] = "passed"
        self.assertEqual(model.next_step(run), "gate")

    def test_review_asks_each_required_role_then_checks(self):
        run = _tdd_run("review")
        run["candidate"] = {"commit": "c1"}
        run["required_reviews"] = ["reviewer", "ux-copy"]
        self.assertEqual(model.next_step(run), "reviewer: review")
        run["reviews"].append({"role": "reviewer", "commit": "c1", "voided": False,
                               "verdict": "approve", "session": "s1"})
        self.assertEqual(model.next_step(run), "ux-copy: review")
        run["reviews"].append({"role": "ux-copy", "commit": "c1", "voided": False,
                               "verdict": "approve", "session": "s2"})
        self.assertEqual(model.next_step(run), "check")

    def test_a_review_of_another_candidate_does_not_count(self):
        run = _tdd_run("review")
        run["candidate"] = {"commit": "c2"}
        run["reviews"].append({"role": "reviewer", "commit": "c1", "voided": False,
                               "verdict": "approve", "session": "s1"})
        self.assertEqual(model.next_step(run), "reviewer: review")

    def test_terminal_and_paused_lines(self):
        self.assertEqual(model.next_step(_tdd_run("local-verified")), "human: merge")
        stopped = _tdd_run("stopped")
        stopped["stop_reason"] = "abandoned"
        self.assertEqual(model.next_step(stopped), "stopped: abandoned")
        paused = _tdd_run("paused")
        paused["prior_phase"] = "tdd"
        self.assertEqual(model.next_step(paused), "human: resume (was tdd)")
        paused["escalations"].append({"question": "which?", "answer": None})
        self.assertEqual(model.next_step(paused), "human: answer 'which?'")
        self.assertEqual(model.next_step({"phase": "unadmitted"}), "prepare")


class NewTddTest(unittest.TestCase):
    def test_the_record_has_the_contract_shape(self):
        tdd = model.new_tdd({}, {"test_command": TEST_COMMAND}, "abc123")
        self.assertEqual((tdd["step"], tdd["mode"], tdd["cycle"]), ("analyzer", "initial", 0))
        self.assertEqual(tdd["attempts"], {"analyzer": 0, "red": 0, "green": 0, "refactor": 0})
        self.assertEqual(tdd["test_command"], TEST_COMMAND)
        self.assertEqual(tdd["test_globs"], ["tests/**"])
        self.assertIsNone(tdd["lint_command"])
        self.assertEqual(tdd["assertion_pattern"], "AssertionError")
        self.assertEqual(tdd["red_stub_max_lines"], 10)
        self.assertEqual((tdd["refactor_skip_max_lines"], tdd["lines_since_refactor"]), (5, 0))
        self.assertEqual(tdd["last_good_commit"], "abc123")
        for empty in ("items", "gaps", "directives", "history"):
            self.assertEqual(tdd[empty], [])
        self.assertEqual(tdd["locked"], {})
        for none in ("current", "red", "red_commit", "green_commit", "final",
                     "last_error", "findings"):
            self.assertIsNone(tdd[none])
        for gone in ("marker", "hidden_globs", "covers"):
            self.assertNotIn(gone, tdd)

    def test_settings_come_from_the_merged_task_then_the_profile(self):
        merged = profile.apply(
            profile.parse(json.dumps({"version": 1, "test_command": TEST_COMMAND,
                                      "test_globs": ["spec/**"],
                                      "lint_command": ["ruff", "check"]})),
            {"id": "t"})
        tdd = model.new_tdd(merged, merged, None)
        self.assertEqual(tdd["test_globs"], ["spec/**"])
        self.assertEqual(tdd["lint_command"], ["ruff", "check"])
        from_profile = model.new_tdd({"test_command": ["t"], "test_globs": ["a/**"]}, {})
        self.assertEqual((from_profile["test_command"], from_profile["test_globs"]),
                         (["t"], ["a/**"]))

    def test_no_test_command_is_refused(self):
        with self.assertRaises(StateError):
            model.new_tdd({}, {})

    def test_items_get_ids_that_are_never_reused(self):
        tdd = model.new_tdd({}, {"test_command": TEST_COMMAND})
        spec = {"text": "greets", "kind": "behavior", "source": "AC 1", "needed": "",
                "criteria": [0]}
        first, second = model.new_item(tdd, spec), model.new_item(tdd, spec)
        self.assertEqual((first["id"], second["id"]), ("T1", "T2"))
        self.assertEqual((first["status"], first["test_file"], first["defeats"]),
                         ("todo", None, None))
        self.assertEqual(first["criteria"], [0])

    def test_attempts_count_and_remember_their_peak(self):
        tdd = model.new_tdd({}, {"test_command": TEST_COMMAND})
        self.assertEqual(model.bump_attempt(tdd, "green"), 1)
        self.assertEqual(model.bump_attempt(tdd, "green"), 2)
        tdd["attempts"]["green"] = 0
        self.assertEqual(model.bump_attempt(tdd, "red"), 1)
        self.assertEqual(tdd["attempts_peak"], 2)


SPEC = {"id": "U1", "owns": ["src/a.py"], "tests": ["tests/test_a.py"], "after": [],
        "needed": ""}
ITEM = {"text": "greets", "kind": "behavior", "source": "AC 1", "needed": "", "criteria": [0]}


class LaneStateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = self.tmp.name
        os.makedirs(os.path.join(self.home, "runs"))
        self.run = _tdd_run("tdd", "lanes")
        self.run["task_id"] = "demo-1"
        self.run["tdd"]["last_good_commit"] = "abc"

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_lane_is_a_tdd_record_with_a_unit_and_its_own_names(self):
        lane = model.new_lane(self.home, self.run, SPEC, "active")
        self.assertEqual((lane["unit"], lane["status"], lane["step"]), ("U1", "active", "red"))
        self.assertEqual((lane["owns"], lane["tests"], lane["after"]),
                         (["src/a.py"], ["tests/test_a.py"], []))
        self.assertEqual(lane["branch"], "t12/demo-1/U1")
        self.assertEqual(lane["worktree"], os.path.join(self.home, "worktrees", "demo-1@U1"))
        self.assertEqual((lane["budget"], lane["last_good_commit"], lane["fork_commit"]),
                         ({}, "abc", None))
        for key in model.TDD_SETTINGS:
            self.assertEqual(lane[key], self.run["tdd"][key])
        for key in ("items", "history", "locked", "attempts", "cycle"):
            self.assertIn(key, lane)

    def test_lane_items_are_prefixed_with_the_unit(self):
        lane = model.new_lane(self.home, self.run, SPEC, "active")
        ids = [model.new_item(lane, ITEM)["id"] for _ in range(2)]
        self.assertEqual(ids, ["U1-T1", "U1-T2"])
        self.assertEqual(model.new_item(self.run["tdd"], ITEM)["id"], "T1")

    def test_a_lane_record_round_trips_and_lists_in_unit_order(self):
        for unit in ("U2", "U1"):
            lane = model.new_lane(self.home, self.run, {**SPEC, "id": unit}, "waiting")
            model.save_lane(self.home, lane, "demo-1")
        self.assertEqual([lane["unit"] for lane in model.list_lanes(self.home, "demo-1")],
                         ["U1", "U2"])
        self.assertEqual(model.load_lane(self.home, "demo-1", "U1")["status"], "waiting")
        self.assertEqual(model.list_lanes(self.home, "other"), [])
        with self.assertRaises(StateError):
            model.load_lane(self.home, "demo-1", "U9")

    def test_the_lane_table_row_follows_the_lane(self):
        lane = model.new_lane(self.home, self.run, SPEC, "waiting")
        model.sync_lane_row(self.run, lane)
        self.assertEqual(self.run["lanes"], [{"unit": "U1", "status": "waiting",
                                              "branch": "t12/demo-1/U1", "fork_commit": None,
                                              "tip": None, "merged_commit": None}])
        lane.update(status="active", fork_commit="abc", last_good_commit="def")
        model.sync_lane_row(self.run, lane)
        self.assertEqual(len(self.run["lanes"]), 1)
        self.assertEqual((self.run["lanes"][0]["status"], self.run["lanes"][0]["tip"]),
                         ("active", "def"))

    def test_a_lane_lock_is_separate_from_the_run_lock(self):
        model.save_lane(self.home, model.new_lane(self.home, self.run, SPEC, "active"), "demo-1")
        model.save_lane(self.home, model.new_lane(self.home, self.run, {**SPEC, "id": "U2"},
                                                  "active"), "demo-1")
        with model.lane_lock(self.home, "demo-1", "U1"):
            self.assertTrue(model.is_lane_busy(self.home, "demo-1", "U1"))
            self.assertFalse(model.is_lane_busy(self.home, "demo-1", "U2"))
            self.assertFalse(model.is_busy(self.home, "demo-1"))
            with model.run_lock(self.home, "demo-1"), model.lane_lock(self.home, "demo-1", "U2"):
                pass
        self.assertFalse(model.is_lane_busy(self.home, "demo-1", "U1"))

    def test_a_held_lane_lock_fails_as_busy(self):
        model.save_lane(self.home, model.new_lane(self.home, self.run, SPEC, "active"), "demo-1")
        original, model.LOCK_WAIT_S = model.LOCK_WAIT_S, 0.1
        try:
            with model.lane_lock(self.home, "demo-1", "U1"):
                with self.assertRaisesRegex(StateError, "busy"):
                    with model.lane_lock(self.home, "demo-1", "U1"):
                        pass
        finally:
            model.LOCK_WAIT_S = original

    def test_a_lane_lock_needs_a_lane(self):
        with self.assertRaises(StateError):
            with model.lane_lock(self.home, "demo-1", "U1"):
                pass
        with self.assertRaises(StateError):
            with model.lane_lock(self.home, "demo-1", "../x"):
                pass

    def test_the_integration_lock_nests_inside_a_lane_lock_and_outside_the_run_lock(self):
        model.save_lane(self.home, model.new_lane(self.home, self.run, SPEC, "active"), "demo-1")
        with model.lane_lock(self.home, "demo-1", "U1"), \
                model.integration_lock(self.home, "demo-1"), model.run_lock(self.home, "demo-1"):
            pass

    def test_lane_ops_are_counted_in_each_lane(self):
        for unit, ops in (("U1", 2), ("U2", 3)):
            lane = model.new_lane(self.home, self.run, {**SPEC, "id": unit}, "active")
            lane["ops_used"] = ops
            model.save_lane(self.home, lane, "demo-1")
        self.assertEqual(model.lane_ops_used(self.home, "demo-1"), 5)

    def test_lane_next_steps(self):
        lane = model.new_lane(self.home, self.run, {**SPEC, "after": ["U0"]}, "waiting")
        self.assertEqual(model.lane_next(self.run, lane), "waiting: after U0")
        lane["status"] = "active"
        for step in ("red", "green", "refactor"):
            lane["step"] = step
            self.assertEqual(model.lane_next(self.run, lane), f"{step}: {step}")
        lane.update(step="final", status="done")
        self.assertEqual(model.lane_next(self.run, lane), "tdd-step: integrate")
        lane.update(step="red", status="paused", escalations=[
            {"question": "which?", "answer": None}])
        self.assertEqual(model.lane_next(self.run, lane), "human: answer 'which?'")

    def test_the_run_waits_on_lanes_until_every_one_is_merged(self):
        self.run["lanes"] = [{"unit": "U1", "status": "merged"}, {"unit": "U2", "status": "done"}]
        self.assertEqual(model.next_step(self.run), "lanes: in flight")
        self.run["lanes"][1]["status"] = "merged"
        self.assertEqual(model.next_step(self.run), "tdd-step")

    def test_archive_takes_the_lanes_with_the_run(self):
        model.save_lane(self.home, model.new_lane(self.home, self.run, SPEC, "active"), "demo-1")
        model.save_run(self.home, self.run)
        base = model.archive_run(self.home, self.run)
        self.assertEqual(model.list_lanes(self.home, "demo-1"), [])
        self.assertTrue(os.path.isfile(os.path.join(self.home, "runs", base, "lanes", "U1.json")))

    def test_a_run_of_an_older_version_is_refused(self):
        self.assertEqual(model.VERSION, 3)
        self.run["version"] = 2
        model.save_run(self.home, self.run)
        with self.assertRaisesRegex(StateError, "unsupported run version"):
            model.load_run(self.home, "demo-1")


class MaxLanesTest(unittest.TestCase):
    def test_the_default_is_three_and_a_task_or_profile_sets_it(self):
        self.assertEqual(model.new_tdd({}, {"test_command": TEST_COMMAND})["max_lanes"], 3)
        merged = profile.apply(profile.parse(json.dumps(
            {"version": 1, "test_command": TEST_COMMAND, "max_lanes": 2})), {"id": "t"})
        self.assertEqual(model.new_tdd(merged, merged)["max_lanes"], 2)
        merged = profile.apply(profile.parse(json.dumps(
            {"version": 1, "test_command": TEST_COMMAND, "max_lanes": 2})),
            {"id": "t", "max_lanes": 1})
        self.assertEqual(model.new_tdd(merged, merged)["max_lanes"], 1)

    def test_max_lanes_must_be_a_positive_integer(self):
        for bad in (0, -1, True, "2"):
            with self.assertRaises(StateError):
                profile.parse(json.dumps({"version": 1, "max_lanes": bad}))


class NewRunTest(unittest.TestCase):
    TASK = {"id": "t1", "acceptance_criteria": ["it works"],
            "gates": [{"name": "unit", "command": ["true"]}]}

    def test_a_new_run_has_the_tdd_budgets_and_no_tdd_record_yet(self):
        run = model.new_run(self.TASK, "/repo", "/home", "abc", profile.parse(None)["roles"])
        self.assertEqual(run["version"], model.VERSION)
        self.assertEqual({k: run["budgets"][k] for k in
                          ("op_limit", "repair_cap", "cycle_limit", "attempt_limit")},
                         {"op_limit": 300, "repair_cap": 3, "cycle_limit": 50, "attempt_limit": 3})
        self.assertIsNone(run["tdd"])
        for gone in ("red", "replay"):
            self.assertNotIn(gone, run)
        self.assertEqual(run["required_reviews"], ["reviewer"])

    def test_a_task_sets_its_own_tdd_budgets(self):
        task = dict(self.TASK, cycle_limit=7, attempt_limit=2)
        run = model.new_run(task, "/repo", "/home", "abc", profile.parse(None)["roles"])
        self.assertEqual((run["budgets"]["cycle_limit"], run["budgets"]["attempt_limit"]), (7, 2))
        self.assertEqual(run["budget_source"], "task")


class RepairOrPauseTest(unittest.TestCase):
    def test_a_gate_failure_goes_back_to_green_in_repair_mode(self):
        run = _tdd_run("gates", "done", attempts={"analyzer": 0, "red": 0, "green": 2, "refactor": 0})
        self.assertEqual(model.repair_or_pause(run, "gate", "ref:gate-1"), "tdd")
        self.assertEqual(run["phase"], "tdd")
        self.assertEqual(run["budgets"]["repairs_used"], 1)
        tdd = run["tdd"]
        self.assertEqual((tdd["step"], tdd["mode"]), ("green", "repair"))
        self.assertEqual(tdd["findings"], {"source": "gate", "report": "ref:gate-1"})
        self.assertEqual(tdd["attempts"]["green"], 0)
        self.assertEqual(model.next_step(run), "green: green")

    def test_changes_requested_goes_back_to_the_analyzer_in_review_fix_mode(self):
        run = _tdd_run("review", "done")
        self.assertEqual(model.repair_or_pause(run, "review", "ref:review-1"), "tdd")
        tdd = run["tdd"]
        self.assertEqual((run["phase"], tdd["step"], tdd["mode"]),
                         ("tdd", "analyzer", "review-fix"))
        self.assertEqual(tdd["findings"], {"source": "review", "report": "ref:review-1"})
        self.assertEqual(model.next_step(run), "analyzer: analyze")

    def test_the_last_repair_under_the_cap_still_repairs(self):
        run = _tdd_run("gates", "done")
        run["budgets"]["repairs_used"] = run["budgets"]["repair_cap"] - 1
        self.assertEqual(model.repair_or_pause(run, "gate", "r"), "tdd")
        self.assertEqual(run["escalations"], [])

    def test_past_the_cap_the_run_pauses_with_an_escalation_not_stopped(self):
        for phase, source in (("gates", "gate"), ("review", "review")):
            run = _tdd_run(phase, "done")
            run["budgets"]["repairs_used"] = run["budgets"]["repair_cap"]
            self.assertEqual(model.repair_or_pause(run, source, "ref:r"), "paused", source)
            self.assertEqual(run["phase"], "paused")
            self.assertEqual(run["prior_phase"], "tdd")
            self.assertNotIn("stop_reason", run)
            [escalation] = run["escalations"]
            self.assertEqual(escalation["kind"], "repair-cap")
            self.assertIn("ref:r", escalation["question"])
            self.assertIsNone(escalation["answer"])
            self.assertIs(model.open_escalation(run), escalation)
            self.assertTrue(model.next_step(run).startswith("human: answer"))
            self.assertEqual(run["tdd"]["findings"]["source"], source)

    def test_an_unknown_source_is_refused_and_changes_nothing(self):
        run = _tdd_run("gates", "done")
        before = copy.deepcopy(run)
        with self.assertRaises(StateError):
            model.repair_or_pause(run, "lint", "r")
        self.assertEqual(run, before)


class TddSummaryTest(unittest.TestCase):
    def test_counts_cycles_items_and_the_final_run(self):
        tdd = model.new_tdd({}, {"test_command": TEST_COMMAND})
        spec = {"text": "x", "kind": "edge", "source": "s", "needed": "", "criteria": []}
        for status in ("done", "done", "todo"):
            model.new_item(tdd, spec)["status"] = status
        tdd.update(step="final", cycle=2,
                   final={"commit": "abc", "tests": "ref:t", "lint": None})
        run = {"tdd": tdd}
        summary = model.tdd_summary(run)
        self.assertEqual((summary["cycles"], summary["items_done"], summary["items_total"]),
                         (2, 2, 3))
        self.assertEqual((summary["final"], summary["final_commit"], summary["final_lint"]),
                         ("ref:t", "abc", None))

    def test_no_final_run_yet(self):
        summary = model.tdd_summary(_tdd_run("tdd"))
        self.assertEqual((summary["cycles"], summary["items_total"], summary["final"]),
                         (0, 0, None))

    def test_measurements_record_cycles_attempts_and_lint_runs(self):
        run = _tdd_run("review", "done", cycle=3, attempts_peak=2,
                       history=[{"green_lint": "r1", "refactor_lint": None},
                                {"green_lint": "r2", "refactor_lint": "r3"}],
                       final={"commit": "c", "tests": "t", "lint": "r4"})
        run["measurements"] = None
        measured = model.record_measurements(run)
        self.assertEqual((measured["cycles"], measured["tdd_attempts"], measured["lint_runs"]),
                         (3, 2, 4))


class BudgetTest(unittest.TestCase):
    def test_time_budget_exhausted(self):
        run = _bare_run("ready")
        run["budgets"]["started_at"] = time.time() - 4000
        with self.assertRaises(BudgetError):
            model.check_budgets(run)

    def test_op_budget_exhausted(self):
        run = _bare_run("ready")
        run["budgets"]["ops_used"] = 100
        with self.assertRaises(BudgetError):
            model.check_budgets(run)

    def test_repair_cap_exceeded(self):
        run = _bare_run("gates")
        run["budgets"]["repairs_used"] = 4
        with self.assertRaises(BudgetError):
            model.check_budgets(run)

    def test_budgets_ok_within_limits(self):
        run = _bare_run("ready")
        self.assertIn("wall_used_s", model.check_budgets(run))


class ChainTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = self.tmp.name
        os.makedirs(os.path.join(self.home, "runs"))
        self.run = _bare_run("ready")
        self.run["task_id"] = "chain-1"
        model.save_run(self.home, self.run)

    def tearDown(self):
        self.tmp.cleanup()

    def _events(self):
        return model.read_events(self.home, "chain-1")

    def _rewrite_events(self, events):
        path = model.events_path(self.home, "chain-1")
        with open(path, "w", encoding="utf-8") as fh:
            for event in events:
                fh.write(json.dumps(event, sort_keys=True) + "\n")

    def test_chain_intact(self):
        model.append_event(self.home, self.run, "prepared", {"a": 1})
        model.append_event(self.home, self.run, "tdd-red", {"commit": "abc"})
        model.append_event(self.home, self.run, "tdd-final", {})
        self.assertEqual(model.verify_chain(self.home, self.run), [])
        events = self._events()
        self.assertEqual([e["seq"] for e in events], [1, 2, 3])
        self.assertEqual(events[0]["prev"], model.genesis_prev("chain-1"))
        self.assertEqual(events[1]["prev"], events[0]["hash"])

    def test_chain_detects_edited_event(self):
        model.append_event(self.home, self.run, "prepared", {})
        model.append_event(self.home, self.run, "tdd-red", {})
        events = self._events()
        events[0]["detail"] = {"tampered": True}
        self._rewrite_events(events)
        problems = model.verify_chain(self.home, self.run)
        self.assertTrue(any("hash mismatch" in p for p in problems), problems)

    def test_chain_detects_deleted_event(self):
        model.append_event(self.home, self.run, "prepared", {})
        model.append_event(self.home, self.run, "tdd-red", {})
        model.append_event(self.home, self.run, "tdd-final", {})
        events = self._events()
        self._rewrite_events([events[0], events[2]])
        problems = model.verify_chain(self.home, self.run)
        self.assertTrue(any("seq" in p or "prev link" in p for p in problems), problems)

    def test_chain_detects_edited_run_record(self):
        model.append_event(self.home, self.run, "prepared", {})
        self.run["budgets"]["op_limit"] = 999
        model.save_run(self.home, self.run)
        problems = model.verify_chain(self.home, self.run)
        self.assertTrue(any("state digest" in p for p in problems), problems)

    def test_chain_detects_broken_prev_link(self):
        model.append_event(self.home, self.run, "prepared", {})
        model.append_event(self.home, self.run, "tdd-red", {})
        events = self._events()
        events[1]["prev"] = "0" * 64
        self._rewrite_events(events)
        problems = model.verify_chain(self.home, self.run)
        self.assertTrue(any("prev link" in p for p in problems), problems)


class ArchiveTest(unittest.TestCase):
    def test_archive_preserves_run_and_events(self):
        with tempfile.TemporaryDirectory() as home:
            os.makedirs(os.path.join(home, "runs"))
            run = _bare_run("stopped")
            run["task_id"] = "demo-1"
            model.save_run(home, run)
            model.append_event(home, run, "stopped", {"reason": "x"})
            base = model.archive_run(home, run)
            self.assertFalse(os.path.exists(model.run_path(home, "demo-1")))
            self.assertTrue(
                os.path.exists(os.path.join(home, "runs", f"{base}.json"))
            )
            self.assertTrue(
                os.path.exists(os.path.join(home, "runs", f"{base}.events.jsonl"))
            )
            archived = store.load_json(os.path.join(home, "runs", f"{base}.json"))
            self.assertEqual(archived["phase"], "stopped")


class ProfileTest(unittest.TestCase):
    GATE = {"name": "unit", "command": ["python3", "-m", "unittest"]}
    TEST = {"test_command": TEST_COMMAND}

    def profile(self, **fields):
        return profile.parse(json.dumps(dict({"version": 1}, **fields)))

    def test_editing_or_adding_a_profile_is_flagged_to_the_reviewer(self):
        for base in (profile.parse(None), self.profile(security_review_paths=["src/auth"])):
            roots = profile.security_roots(base)
            self.assertTrue(model.under(".t12/project.json", roots))
            self.assertFalse(model.under("src/other.py", roots))
        self.assertTrue(model.under("src/auth/login.py", roots))

    def test_the_builtin_roles_are_the_tdd_roles_and_one_reviewer(self):
        roles = {name: spec["kind"] for name, spec in profile.parse(None)["roles"].items()}
        self.assertEqual(roles, {"analyzer": "analyzer", "red": "red", "green": "green",
                                 "refactor": "refactor", "reviewer": "review"})
        self.assertEqual(profile.review_roles(profile.parse(None)["roles"]), ["reviewer"])

    def test_a_builtin_role_cannot_change_kind_and_old_roles_are_not_builtin(self):
        with self.assertRaises(StateError):
            self.profile(roles={"green": {"kind": "review"}})
        with self.assertRaises(StateError):
            self.profile(required_reviews=["security"])
        with self.assertRaises(StateError):
            self.profile(roles={"builder": {"kind": "builder"}})

    def test_a_malformed_profile_on_the_base_is_refused(self):
        for text in ("[]", "not json", '{"version": 2}', '{"version": 1, "gatez": []}',
                     '{"version": 1, "protected_paths": ["../x"]}',
                     '{"version": 1, "op_limit": true}',
                     '{"version": 1, "test_command": []}',
                     '{"version": 1, "test_command": "pytest"}',
                     '{"version": 1, "test_command": [1]}',
                     '{"version": 1, "test_globs": []}',
                     '{"version": 1, "test_globs": ["/abs/**"]}',
                     '{"version": 1, "test_globs": ["../x/**"]}',
                     '{"version": 1, "lint_command": []}',
                     '{"version": 1, "lint_command": "ruff"}',
                     '{"version": 1, "assertion_pattern": "("}',
                     '{"version": 1, "assertion_pattern": null}',
                     '{"version": 1, "failure_count_pattern": "no group"}',
                     '{"version": 1, "red_stub_max_lines": -1}',
                     '{"version": 1, "red_stub_max_lines": true}',
                     '{"version": 1, "refactor_skip_max_lines": -1}',
                     '{"version": 1, "refactor_skip_max_lines": false}',
                     '{"version": 1, "cycle_limit": 0}',
                     '{"version": 1, "attempt_limit": "3"}'):
            with self.assertRaises(StateError, msg=text):
                profile.parse(text)

    def test_the_tdd_keys_are_validated_and_defaulted(self):
        merged = profile.apply(self.profile(**self.TEST), {"id": "t"})
        self.assertEqual(merged["test_command"], TEST_COMMAND)
        self.assertEqual(merged["test_globs"], ["tests/**"])
        self.assertIsNone(merged["lint_command"])
        self.assertEqual(merged["assertion_pattern"], "AssertionError")
        self.assertIsNone(merged["failure_count_pattern"])
        self.assertEqual(merged["red_stub_max_lines"], 10)
        self.assertEqual(merged["refactor_skip_max_lines"], 5)
        full = self.profile(
            test_command=["pytest", "-q"], test_globs=["tests/**", "**/*_test.py"],
            lint_command=["ruff", "check"], assertion_pattern="assert|Error",
            failure_count_pattern=r"(\d+) failed", red_stub_max_lines=3,
            refactor_skip_max_lines=0, cycle_limit=9, attempt_limit=2)
        merged = profile.apply(full, {"id": "t"})
        self.assertEqual(merged["test_globs"], ["tests/**", "**/*_test.py"])
        self.assertEqual(merged["lint_command"], ["ruff", "check"])
        self.assertEqual(merged["failure_count_pattern"], r"(\d+) failed")
        self.assertEqual((merged["red_stub_max_lines"], merged["cycle_limit"],
                          merged["attempt_limit"], merged["refactor_skip_max_lines"]),
                         (3, 9, 2, 0))

    def test_test_command_is_required_from_the_profile_or_the_task(self):
        with self.assertRaises(StateError):
            profile.apply(self.profile(), {"id": "t"})
        with self.assertRaises(StateError):
            profile.apply(profile.parse(None), {"id": "t"})
        merged = profile.apply(profile.parse(None), dict({"id": "t"}, **self.TEST))
        self.assertEqual(merged["test_command"], TEST_COMMAND)

    def test_a_task_may_set_the_tdd_keys_only_where_the_profile_does_not(self):
        base = self.profile(**self.TEST)
        merged = profile.apply(base, {"test_command": TEST_COMMAND,
                                      "lint_command": ["ruff", "check"]})
        self.assertEqual(merged["lint_command"], ["ruff", "check"])
        for loosen in ({"test_command": ["true"]}, {"test_globs": ["docs/**"]},
                       {"assertion_pattern": "."}, {"red_stub_max_lines": 1000},
                       {"refactor_skip_max_lines": 1000}):
            with self.assertRaises(StateError, msg=loosen):
                profile.apply(self.profile(test_command=TEST_COMMAND, test_globs=["tests/**"],
                                           assertion_pattern="AssertionError",
                                           red_stub_max_lines=10, refactor_skip_max_lines=5),
                              {**self.TEST, **loosen})
        with self.assertRaises(StateError):
            profile.apply(self.profile(lint_command=["ruff"], **self.TEST),
                          {"lint_command": ["true"]})

    def test_a_task_without_settings_takes_the_profile(self):
        merged = profile.apply(self.profile(gates=[self.GATE], op_limit=9, **self.TEST),
                               {"id": "t"})
        self.assertEqual(merged["gates"], [self.GATE])
        self.assertEqual(merged["op_limit"], 9)

    def test_a_task_can_tighten_but_never_loosen_the_profile(self):
        base = self.profile(gates=[self.GATE], required_reviews=["reviewer"],
                            sandbox_adapter="docker", require_probes=True,
                            roles={"ux-copy": {"kind": "review"}}, **self.TEST)
        extra = {"name": "lint", "command": ["ruff", "check"]}
        merged = profile.apply(base, {"gates": [extra], "required_reviews": ["ux-copy"],
                                      "require_probes": False})
        self.assertEqual(merged["gates"], [self.GATE, extra])
        self.assertEqual(merged["required_reviews"], ["reviewer", "ux-copy"])
        self.assertTrue(merged["require_probes"])
        for loosen in ({"sandbox_adapter": "none"}, {"gates": [self.GATE]},
                       {"protected_paths": []}, {"security_review_paths": []}):
            with self.assertRaises(StateError, msg=loosen):
                profile.apply(base, loosen)

    def test_a_task_may_only_narrow_the_profiles_verify_deps(self):
        base = self.profile(verify_deps=[".venv", "node_modules"], **self.TEST)
        self.assertEqual(profile.apply(base, {"verify_deps": [".venv"]})["verify_deps"], [".venv"])
        with self.assertRaises(StateError):
            profile.apply(base, {"verify_deps": ["vendor"]})

    def test_verify_deps_may_not_overlap_a_protected_path(self):
        base = self.profile(protected_paths=["guards"], **self.TEST)
        for dep in ("guards/__pycache__", "guards"):
            with self.assertRaises(StateError, msg=dep):
                profile.apply(base, {"verify_deps": [dep]})

    def test_a_mistyped_task_value_is_a_clear_error(self):
        base = self.profile(gates=[self.GATE], required_reviews=["reviewer"], **self.TEST)
        for bad in ({"gates": None}, {"required_reviews": None}, {"op_limit": "9"},
                    {"cycle_limit": 0}, {"attempt_limit": True}, {"test_command": "pytest"},
                    {"lint_command": 3}):
            with self.assertRaises(StateError, msg=bad):
                profile.apply(base, bad)

    def test_a_profile_that_lists_no_deps_allows_none(self):
        with self.assertRaises(StateError):
            profile.apply(self.profile(**self.TEST), {"verify_deps": [".venv"]})
        self.assertEqual(profile.apply(profile.parse(None),
                                       dict({"verify_deps": [".venv"]}, **self.TEST))
                         ["verify_deps"], [".venv"])

    def test_a_dep_beside_a_protected_file_is_refused(self):
        base = self.profile(protected_paths=["guards/test_guard.py"],
                            verify_deps=["guards/__pycache__", ".venv"], **self.TEST)
        with self.assertRaises(StateError):
            profile.apply(base, {"verify_deps": ["guards/__pycache__"]})
        root_file = self.profile(protected_paths=["AGENTS.md"], verify_deps=[".venv"], **self.TEST)
        self.assertEqual(profile.apply(root_file, {})["verify_deps"], [".venv"])

    def test_a_mistyped_list_entry_is_a_clear_error(self):
        base = self.profile(gates=[self.GATE], verify_deps=[".venv"], **self.TEST)
        for bad in ({"verify_deps": [1]}, {"gates": [1]}, {"gates": [{"name": ["x"]}]},
                    {"required_reviews": [None]}):
            with self.assertRaises(StateError, msg=bad):
                profile.apply(base, bad)

    def test_intake_is_validated_and_only_the_profile_may_set_it(self):
        intake = {"adapter": "github-issues", "repo": "o/r", "label": "factory"}
        self.assertEqual(self.profile(intake=intake)["intake"], intake)
        self.assertEqual(self.profile(intake={"adapter": "gitlab-issues", "repo": "g/s/p"})["intake"],
                         {"adapter": "gitlab-issues", "repo": "g/s/p"})
        for bad in ("github-issues", {}, dict(intake, token="x"), dict(intake, repo="o"),
                    dict(intake, repo="o r/x"), dict(intake, adapter="../x"),
                    dict(intake, label=""), dict(intake, label=3)):
            with self.assertRaises(StateError, msg=bad):
                self.profile(intake=bad)
        with self.assertRaises(StateError):
            profile.apply(self.profile(**self.TEST), {"intake": intake})

    def test_the_task_sets_its_own_budget(self):
        merged = profile.apply(self.profile(op_limit=9, **self.TEST),
                               {"op_limit": 40, "cycle_limit": 5, "attempt_limit": 2})
        self.assertEqual((merged["op_limit"], merged["cycle_limit"], merged["attempt_limit"]),
                         (40, 5, 2))

    def test_this_repos_profile_and_example_task_agree(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, ".t12", "project.json"), encoding="utf-8") as fh:
            own = profile.parse(fh.read())
        with open(os.path.join(root, "examples", "task.json"), encoding="utf-8") as fh:
            task = json.load(fh)
        self.assertEqual(own["required_reviews"], ["reviewer"])
        merged = profile.apply(own, dict(task, gates=[]))
        self.assertEqual(merged["test_command"], TEST_COMMAND)
        self.assertEqual(merged["test_globs"], ["tests/**"])
        self.assertIsNone(merged["lint_command"])
        self.assertEqual(task["test_command"], TEST_COMMAND)
        self.assertEqual(task["required_reviews"], ["reviewer"])


class SkillsProfileTest(unittest.TestCase):
    SHA = "0123456789abcdef" * 4
    REV = "d524ed287eef22757297827ce42e3cad88abd20c"
    PIN = {"repo": "antfu/skills", "rev": REV, "path": "skills/vitest",
           "include": ["SKILL.md", "references/**"], "roles": ["red"],
           "when": {"dependency": "vitest"}, "sha256": SHA}

    def profile(self, **pins):
        return profile.parse(json.dumps({"version": 1, "skills": pins}))

    def test_a_valid_skills_map_is_parsed_with_defaults_filled(self):
        wrapped = {"repo": "o/n", "rev": self.REV, "path": ".claude/commands/x.md",
                   "wrap": True, "roles": ["reviewer", "red", "red"], "sha256": self.SHA}
        parsed = self.profile(vitest=self.PIN, wrapped=wrapped)["skills"]
        self.assertEqual(parsed["vitest"], dict(self.PIN, wrap=False))
        self.assertEqual(parsed["wrapped"], dict(wrapped, include=[], when=None,
                                                 roles=["reviewer", "red"]))
        self.assertNotIn("skills", profile.parse(None))

    def test_a_malformed_skill_is_refused(self):
        pin = self.PIN
        for bad in (dict(pin, repo="antfu"), dict(pin, repo="a b/c"), dict(pin, repo=3),
                    dict(pin, rev="d524ed2"), dict(pin, rev="D" * 40), dict(pin, rev=None),
                    dict(pin, path=""), dict(pin, path="/abs"), dict(pin, path="a/../b"),
                    dict(pin, include=[]), dict(pin, include="SKILL.md"),
                    dict(pin, include=["../x"]), dict(pin, include=[""]),
                    dict(pin, wrap="yes"), dict(pin, wrap=True),
                    dict(pin, roles=[]), dict(pin, roles=["nobody"]), dict(pin, roles="red"),
                    dict(pin, when={}), dict(pin, when={"dependency": 3}),
                    dict(pin, when={"files": []}), dict(pin, when={"files": "*.ts"}),
                    dict(pin, when={"dependency": "a", "files": ["*"]}),
                    dict(pin, when={"other": "x"}), dict(pin, when="always"),
                    dict(pin, sha256="abc"), dict(pin, sha256=self.SHA.upper()),
                    dict(pin, sha256=None), dict(pin, extra=1), "vitest", None):
            with self.assertRaises(StateError, msg=bad):
                self.profile(vitest=bad)
        no_sha = {k: v for k, v in pin.items() if k != "sha256"}
        with self.assertRaises(StateError):
            self.profile(vitest=no_sha)
        with self.assertRaises(StateError):
            self.profile(**{"../escape": pin})
        for bad in ([], "x", None):
            with self.assertRaises(StateError, msg=bad):
                profile.parse(json.dumps({"version": 1, "skills": bad}))

    def test_a_role_the_project_defines_can_get_a_skill(self):
        text = json.dumps({"version": 1, "roles": {"docs": {"kind": "green"}},
                           "skills": {"x": dict(self.PIN, roles=["docs"])}})
        self.assertEqual(profile.parse(text)["skills"]["x"]["roles"], ["docs"])

    def test_a_task_file_cannot_bring_skills(self):
        base = profile.parse(json.dumps({"version": 1, "test_command": TEST_COMMAND}))
        with self.assertRaises(StateError):
            profile.apply(base, {"skills": {"x": self.PIN}})


class ApplicableSkillsTest(unittest.TestCase):
    SHA = SkillsProfileTest.SHA
    REV = SkillsProfileTest.REV

    def pin(self, roles, when=None):
        return {"repo": "o/n", "rev": self.REV, "path": "skills/x",
                "include": ["SKILL.md"], "roles": roles, "when": when, "sha256": self.SHA}

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = tmp.name

    def git(self, *args):
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
        out = subprocess.run(["git", *args], cwd=self.repo, env=env, check=True,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return out.stdout.decode().strip()

    def commit(self, files):
        if not os.path.isdir(os.path.join(self.repo, ".git")):
            self.git("init", "-q")
        for name, text in files.items():
            path = os.path.join(self.repo, name)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "c")
        return self.git("rev-parse", "HEAD")

    def run_profile(self, **skills):
        return profile.parse(json.dumps({"version": 1, "skills": skills}))

    def names(self, run_profile, role, commit):
        return [s["name"] for s in profile.applicable_skills(run_profile, role, self.repo, commit)]

    def test_the_roles_pins_come_back_as_name_rev_and_sha(self):
        base = self.commit({"a.txt": "a\n"})
        run = self.run_profile(zeta=self.pin(["green"]), alpha=self.pin(["green", "red"]),
                               only_red=self.pin(["red"]))
        self.assertEqual(profile.applicable_skills(run, "green", self.repo, base),
                         [{"name": "alpha", "rev": self.REV, "sha256": self.SHA},
                          {"name": "zeta", "rev": self.REV, "sha256": self.SHA}])
        self.assertEqual(profile.applicable_skills(run, "analyzer", self.repo, base), [])
        self.assertEqual(profile.applicable_skills(profile.parse(None), "red", self.repo, base), [])

    def test_a_dependency_is_read_from_package_json_at_the_commit(self):
        run = self.run_profile(vitest=self.pin(["red"], {"dependency": "vitest"}))
        none = self.commit({"a.txt": "a\n"})
        dev = self.commit({"package.json": json.dumps({"devDependencies": {"vitest": "^2"}})})
        prod = self.commit({"package.json": json.dumps({"dependencies": {"vitest": "^2"}})})
        other = self.commit({"package.json": json.dumps({"dependencies": {"jest": "^2"}})})
        got = {c: self.names(run, "red", c) for c in (none, dev, prod, other)}
        self.assertEqual(got, {none: [], dev: ["vitest"], prod: ["vitest"], other: []})
        self.commit({"package.json": json.dumps({"devDependencies": {"vitest": "^3"}})})
        self.assertEqual(self.names(run, "red", other), [])

    def test_files_match_tracked_paths_at_the_commit_with_t12_globs(self):
        run = self.run_profile(ui=self.pin(["green"], {"files": ["**/*.tsx", "*.vue"]}))
        plain = self.commit({"src/a.ts": "a\n"})
        tsx = self.commit({"web/deep/App.tsx": "x\n"})
        vue = self.commit({"Root.vue": "x\n"})
        self.assertEqual(self.names(run, "green", plain), [])
        self.assertEqual(self.names(run, "green", tsx), ["ui"])
        self.assertEqual(self.names(run, "green", vue), ["ui"])
        with open(os.path.join(self.repo, "Untracked.tsx"), "w") as fh:
            fh.write("x\n")
        self.assertEqual(self.names(run, "green", plain), [])

    def test_a_broken_package_json_is_an_error_not_a_silent_skip(self):
        run = self.run_profile(vitest=self.pin(["red"], {"dependency": "vitest"}))
        bad = self.commit({"package.json": "{not json"})
        with self.assertRaises(StateError):
            profile.applicable_skills(run, "red", self.repo, bad)
        listy = self.commit({"package.json": "[]"})
        with self.assertRaises(StateError):
            profile.applicable_skills(run, "red", self.repo, listy)


class IntegrationStateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = self.tmp.name
        os.makedirs(os.path.join(self.home, "runs"))
        self.run = _tdd_run("tdd", "lanes")
        self.run["task_id"] = "demo-1"

    def tearDown(self):
        self.tmp.cleanup()

    def lane(self, unit, **fields):
        lane = model.new_lane(self.home, self.run, {**SPEC, "id": unit}, "active")
        lane.update(fields)
        return lane

    def test_the_lane_that_finished_first_integrates_first_and_ties_go_by_unit_id(self):
        done = {"status": "done", "step": "final"}
        lanes = [self.lane("U1", finished_at=5.0, **done), self.lane("U2", finished_at=3.0, **done),
                 self.lane("U3", step="red")]
        self.assertEqual(model.integrate_head(lanes), "U2")
        lanes[0]["finished_at"] = 3.0
        self.assertEqual(model.integrate_head(lanes), "U1")
        lanes[0]["status"] = lanes[1]["status"] = "merged"
        self.assertIsNone(model.integrate_head(lanes))

    def test_a_finished_lane_waits_for_the_head_and_for_the_integration_green(self):
        done = {"status": "done", "step": "final"}
        lanes = [self.lane("U1", finished_at=1.0, **done), self.lane("U2", finished_at=2.0, **done)]
        self.assertEqual(model.lane_next(self.run, lanes[0], lanes), "tdd-step: integrate")
        self.assertEqual(model.lane_next(self.run, lanes[1], lanes), "waiting: after U1")
        self.run["tdd"].update(step="integration-green", pending_merge={"unit": "U1", "owns": []})
        self.assertEqual(model.lane_next(self.run, lanes[0], lanes), "green: green")
        self.assertEqual(model.lane_next(self.run, lanes[1], lanes),
                         "waiting: after integration-green")
        self.assertEqual(model.pending_merge(self.run)["unit"], "U1")
        self.run["tdd"]["step"] = "lanes"
        self.assertIsNone(model.pending_merge(self.run))
        self.assertEqual(model.lane_next(self.run, self.lane("U1", status="merged")), "merged")

    def test_merged_lanes_ops_are_not_counted_twice(self):
        for unit, status, ops in (("U1", "merged", 4), ("U2", "active", 3)):
            model.save_lane(self.home, self.lane(unit, status=status, ops_used=ops), "demo-1")
        self.assertEqual(model.lane_ops_used(self.home, "demo-1"), 3)

    def test_a_run_counts_its_merge_attempts_and_failures_in_its_measurements(self):
        run = _tdd_run("tdd", "lanes")
        run["budgets"]["started_at"] = time.time()
        self.assertEqual((run["tdd"]["integrations"], run["tdd"]["integration_failures"]), (0, []))
        run["tdd"]["integrations"] = 4
        run["tdd"]["integration_failures"] = [{"route": "own-tests"}, {"route": "conflict"}]
        measured = model.record_measurements(run)
        self.assertEqual((measured["integrations"], measured["integration_failures"]), (4, 2))

    def test_a_calibrated_max_lanes_is_the_default_the_task_and_profile_override(self):
        self.assertEqual(model.new_tdd({}, {"test_command": TEST_COMMAND}, "b", 2)["max_lanes"], 2)
        self.assertEqual(model.new_tdd({}, {"test_command": TEST_COMMAND, "max_lanes": 3}, "b",
                                       2)["max_lanes"], 3)
        self.assertEqual(model.new_tdd({}, {"test_command": TEST_COMMAND}, "b")["max_lanes"],
                         model.DEFAULT_MAX_LANES)


if __name__ == "__main__":
    unittest.main()
