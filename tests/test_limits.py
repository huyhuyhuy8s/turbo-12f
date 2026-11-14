"""Tests for limits (Phase 5): percentile math, calibration from
completed runs, censoring, limits.json round-trip, and reporting.
"""

import json
import os
import tempfile
import unittest

from t12 import limits, store
from t12.errors import StateError


_ESCALATION = {"question": "repair cap exceeded", "kind": "repair-cap", "answer": None}


def _record(task_id, phase, measurements=None, stop_reason=None, budgets=None,
            escalations=None):
    return {
        "task_id": task_id,
        "phase": phase,
        "stop_reason": stop_reason,
        "measurements": measurements,
        "budgets": budgets or {
            "time_limit_s": 3600, "op_limit": 300, "repair_cap": 3,
            "cycle_limit": 50, "attempt_limit": 3,
            "ops_used": 0, "repairs_used": 0,
        },
        "budget_source": "builtin",
        "escalations": escalations or [],
    }


def _measurements(wall, ops, repairs, cycles=0, attempts=0):
    return {"wall_used_s": wall, "ops_used": ops, "repairs_used": repairs,
            "cycles": cycles, "tdd_attempts": attempts, "lint_runs": 0,
            "gates": 1, "finished_at": 0.0}


class PercentileTest(unittest.TestCase):
    def test_nearest_rank(self):
        self.assertEqual(limits.percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 50), 5)
        self.assertEqual(limits.percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 90), 9)
        self.assertEqual(limits.percentile([42], 90), 42)
        self.assertEqual(limits.percentile([3, 1, 2], 50), 2)

    def test_round_up(self):
        self.assertEqual(limits.round_up(61, 60), 120)
        self.assertEqual(limits.round_up(60, 60), 60)
        self.assertEqual(limits.round_up(0, 5), 0)
        self.assertEqual(limits.round_up(7, 5), 10)

    def test_summarize(self):
        self.assertIsNone(limits.summarize([]))
        summary = limits.summarize([10, 20, 30])
        self.assertEqual((summary["n"], summary["p50"], summary["p90"], summary["max"]),
                         (3, 20, 30, 30))


class RecommendTest(unittest.TestCase):
    def _completed(self, rows):
        return [{"measurements": _measurements(*row)} for row in rows]

    def test_too_few_runs(self):
        self.assertIsNone(limits.recommend(self._completed([(10, 5, 0), (20, 6, 0)])))
        self.assertIsNone(limits.recommend([]))

    def test_floors_apply_to_toy_runs(self):
        rec = limits.recommend(self._completed([(10, 5, 0), (20, 6, 0), (30, 7, 1)]))
        self.assertEqual(rec["sample"], 3)
        self.assertEqual(rec["recommended"],
                         {"time_limit_s": 600, "op_limit": 20, "repair_cap": 2,
                          "cycle_limit": 5, "attempt_limit": 2})

    def test_p90_drives_recommendation(self):
        rec = limits.recommend(self._completed([
            (900, 40, 1, 4, 1), (1200, 50, 2, 6, 1), (1500, 60, 2, 8, 2),
            (1800, 70, 3, 9, 2), (2000, 80, 3, 12, 3),
        ]))
        self.assertEqual(rec["recommended"]["time_limit_s"], 2040)
        self.assertEqual(rec["recommended"]["op_limit"], 80)
        self.assertEqual(rec["recommended"]["repair_cap"], 3)
        self.assertEqual(rec["recommended"]["cycle_limit"], 12)
        self.assertEqual(rec["recommended"]["attempt_limit"], 3)
        self.assertEqual(rec["stats"]["cycle_limit"]["max"], 12)

    def test_runs_without_tdd_measurements_leave_those_keys_at_the_builtins(self):
        completed = [{"measurements": {"wall_used_s": 10, "ops_used": 5, "repairs_used": 0}}
                     for _ in range(3)]
        rec = limits.recommend(completed)
        self.assertEqual(rec["recommended"]["cycle_limit"], limits.BUILTIN_DEFAULTS["cycle_limit"])
        self.assertEqual(rec["recommended"]["attempt_limit"], limits.BUILTIN_DEFAULTS["attempt_limit"])
        self.assertIsNone(rec["stats"]["cycle_limit"])
        self.assertIn("cycles n/a", limits.report_text("/h", {
            "runs": [], "unreadable": [], "completed": completed, "censored": [],
            "cap_hits": {"repair": 0, "ops": 0, "time": 0}}, rec))

    def test_the_builtin_defaults(self):
        self.assertEqual(limits.BUILTIN_DEFAULTS,
                         {"time_limit_s": 3600, "op_limit": 300, "repair_cap": 3,
                          "cycle_limit": 50, "attempt_limit": 3})
        self.assertEqual(set(limits.KEYS), set(limits.BUILTIN_DEFAULTS))
        self.assertEqual(set(limits.KEYS), set(limits.MEASURED))


class ReportTest(unittest.TestCase):
    def test_report_names_censored_runs_and_the_tdd_budgets(self):
        with tempfile.TemporaryDirectory() as home:
            os.makedirs(os.path.join(home, "runs"))
            for i in range(3):
                with open(os.path.join(home, "runs", f"v{i}.json"), "w") as fh:
                    json.dump(_record(f"v{i}", "local-verified",
                                      _measurements(10 + i, 5 + i, 0, 2, 1)), fh)
            with open(os.path.join(home, "runs", "capped.json"), "w") as fh:
                json.dump(_record("capped", "paused", escalations=[_ESCALATION]), fh)
            data = limits.collect(home)
            text = limits.report_text(home, data, limits.recommend(data["completed"]))
        self.assertIn("paused on the repair cap): capped", text)
        self.assertIn("cycles 2/50", text)
        self.assertIn("cycle_limit=5", text)
        self.assertIn("attempt_limit=2", text)


class CollectTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = self.tmp.name
        os.makedirs(os.path.join(self.home, "runs"))

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, name, record):
        path = os.path.join(self.home, "runs", name)
        with open(path, "w") as fh:
            json.dump(record, fh)

    def test_collect_splits_completed_censored_superseded(self):
        for i in range(3):
            self._write(f"v{i}.json", _record(
                f"v{i}", "local-verified", _measurements(10 + i, 5 + i, 0)))
        self._write("capped.json", _record(
            "capped", "paused", escalations=[_ESCALATION]))
        self._write("abandoned.json", _record("abandoned", "stopped", None,
                                              stop_reason="bad approach"))
        self._write("old.superseded-123.json", _record(
            "old", "local-verified", _measurements(5, 5, 0)))
        with open(os.path.join(self.home, "runs", "broken.json"), "w") as fh:
            fh.write("{not json")
        data = limits.collect(self.home)
        self.assertEqual(len(data["completed"]), 3)
        self.assertEqual(len(data["censored"]), 1)
        self.assertEqual(data["censored"][0]["task_id"], "capped")
        self.assertEqual(len([r for r in data["runs"] if r["superseded"]]), 1)
        self.assertEqual(data["unreadable"], ["broken.json"])
        self.assertEqual(data["cap_hits"]["repair"], 1)
        rec = limits.recommend(data["completed"])
        self.assertEqual(rec["sample"], 3)

    def test_a_run_paused_on_the_repair_cap_is_censored_not_calibrated(self):
        self._write("capped.json", _record(
            "capped", "paused", _measurements(300, 50, 4), escalations=[_ESCALATION]))
        data = limits.collect(self.home)
        self.assertEqual([r["task_id"] for r in data["censored"]], ["capped"])
        self.assertEqual(data["completed"], [])

    def test_a_capped_run_a_human_then_stopped_is_still_censored(self):
        self._write("capped.json", _record(
            "capped", "stopped", stop_reason="gave up", escalations=[_ESCALATION]))
        self.assertEqual([r["task_id"] for r in limits.collect(self.home)["censored"]],
                         ["capped"])

    def test_other_pauses_and_answered_later_runs_are_not_censored(self):
        human = {"question": "which?", "answer": None, "kind": "human"}
        self._write("asked.json", _record("asked", "paused", escalations=[human]))
        self._write("plain.json", _record("plain", "paused"))
        self._write("recovered.json", _record(
            "recovered", "local-verified", _measurements(5, 5, 1),
            escalations=[dict(_ESCALATION, answer="try X")]))
        self._write("later.json", _record(
            "later", "paused", escalations=[_ESCALATION, human]))
        data = limits.collect(self.home)
        self.assertEqual(data["censored"], [])
        self.assertEqual([r["task_id"] for r in data["completed"]], ["recovered"])

    def test_a_stop_reason_of_repair_cap_no_longer_censors(self):
        # Nothing stops on the repair cap any more: it pauses.
        self._write("old.json", _record("old", "stopped", stop_reason="repair-cap"))
        self.assertEqual(limits.collect(self.home)["censored"], [])

    def test_empty_home(self):
        data = limits.collect(self.home)
        self.assertEqual(data["runs"], [])
        self.assertIsNone(limits.recommend(data["completed"]))


class LimitsFileTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_save_load_round_trip(self):
        recommended = {"time_limit_s": 600, "op_limit": 20, "repair_cap": 2,
                       "cycle_limit": 5, "attempt_limit": 2}
        rec = {"sample": 3,
               "stats": {"time_limit_s": {"n": 3, "p50": 20, "p90": 30, "max": 30}},
               "recommended": recommended}
        limits.save_limits(self.home, rec)
        loaded = limits.load_limits(self.home)
        self.assertEqual(loaded, recommended)
        self.assertEqual(store.load_json(limits.limits_path(self.home))["calibrated_from"]["runs"], 3)

    def test_no_file_is_none(self):
        self.assertIsNone(limits.load_limits(self.home))

    def test_partial_keys_fill_from_builtins(self):
        store.save_json(limits.limits_path(self.home), {"op_limit": 40})
        loaded = limits.load_limits(self.home)
        self.assertEqual(loaded["op_limit"], 40)
        self.assertEqual(loaded["time_limit_s"], 3600)
        self.assertEqual((loaded["cycle_limit"], loaded["attempt_limit"]), (50, 3))

    def test_a_file_from_before_the_tdd_budgets_still_loads(self):
        store.save_json(limits.limits_path(self.home),
                        {"time_limit_s": 900, "op_limit": 40, "repair_cap": 2})
        loaded = limits.load_limits(self.home)
        self.assertEqual((loaded["cycle_limit"], loaded["attempt_limit"]), (50, 3))

    def test_the_tdd_limits_must_be_positive_integers(self):
        for bad in ({"cycle_limit": 0}, {"attempt_limit": -1}, {"cycle_limit": "many"}):
            store.save_json(limits.limits_path(self.home), bad)
            with self.assertRaises(StateError, msg=bad):
                limits.load_limits(self.home)

    def test_malformed_file_fails_closed(self):
        with open(limits.limits_path(self.home), "w") as fh:
            fh.write("{oops")
        with self.assertRaises(StateError):
            limits.load_limits(self.home)
        store.save_json(limits.limits_path(self.home), {"op_limit": 0})
        with self.assertRaises(StateError):
            limits.load_limits(self.home)


if __name__ == "__main__":
    unittest.main()
