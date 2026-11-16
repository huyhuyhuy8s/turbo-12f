import json
import os
import subprocess
import tempfile
import unittest

from tests.test_t12 import APP_V1, BIN, TddFlow, task_dict

BOTH_PASS = [{"index": 0, "result": "pass"}, {"index": 1, "result": "pass"}]


class ReviewCriteria(TddFlow, unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        repo = os.path.join(self.tmp.name, "product")
        self.home = os.path.join(self.tmp.name, "state")
        os.makedirs(os.path.join(repo, "tests"))
        self.git(repo, "init", "-b", "main")
        self.git(repo, "config", "user.email", "test@example.com")
        self.git(repo, "config", "user.name", "Test")
        self.write(repo, "app.py", APP_V1)
        self.write(repo, "tests/__init__.py", "")
        self.git(repo, "add", ".")
        self.git(repo, "commit", "-m", "init")
        task_path = os.path.join(self.tmp.name, "task.json")
        with open(task_path, "w") as fh:
            json.dump(task_dict(), fh)
        self.t12("init", "--repo", repo, "--home", self.home)
        self.t12("prepare", "--home", self.home, "--task", task_path)
        self.run_to_review()

    def git(self, cwd, *args):
        result = subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                                text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def write(self, root, rel, content):
        with open(os.path.join(root, rel), "w") as fh:
            fh.write(content)

    def t12(self, *args, expect=0):
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        result = subprocess.run([BIN, *args], capture_output=True, text=True,
                                timeout=120, env=env)
        self.assertEqual(result.returncode, expect,
                         f"t12 {' '.join(args)}: {result.stdout} {result.stderr}")
        return result

    def review_call(self, args):
        """Send a review tool call and return the process, whatever its exit code."""
        path = os.path.join(self.tmp.name, "review-call.json")
        with open(path, "w") as fh:
            json.dump({"tool": "review", "args": args}, fh)
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        return subprocess.run(
            [BIN, "act", "--home", self.home, "--task-id", "demo-1", "--role", "reviewer",
             "--session", "sess-reviewer", "--input", path],
            capture_output=True, text=True, timeout=120, env=env)

    def recorded_reviews(self):
        with open(os.path.join(self.home, "runs", "demo-1.json"), encoding="utf-8") as fh:
            record = json.load(fh)
        return [(r["verdict"], r.get("criteria")) for r in record["reviews"]]

    def test_merge_ready_with_every_criterion_passing_is_recorded_as_approve_with_them(self):
        self.review_call({"verdict": "merge-ready", "findings": [],
                          "verification": "re-ran the unit gate", "criteria": BOTH_PASS})
        self.assertEqual(self.recorded_reviews(), [("approve", BOTH_PASS)])

    def test_blocked_with_a_failing_criterion_is_recorded_as_changes_requested(self):
        one_fails = [{"index": 0, "result": "pass"}, {"index": 1, "result": "fail"}]
        self.review_call({"verdict": "blocked", "findings": [],
                          "verification": "re-ran the unit gate", "criteria": one_fails})
        self.assertEqual(self.recorded_reviews(), [("changes-requested", one_fails)])

    def last_event(self):
        path = os.path.join(self.home, "runs", "demo-1.events.jsonl")
        with open(path, encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()][-1]

    def test_merge_ready_with_a_failing_criterion_is_rejected_naming_it_and_records_nothing(self):
        one_fails = [{"index": 0, "result": "pass"}, {"index": 1, "result": "fail"}]
        done = self.review_call({"verdict": "merge-ready", "findings": [],
                                 "verification": "re-ran the unit gate", "criteria": one_fails})
        event = self.last_event()
        error = (event.get("detail") or {}).get("error", "")
        outcome = {"rejected": done.returncode != 0, "event": event["type"],
                   "names_criterion_1": "criterion 1" in error or "criteria[1]" in error,
                   "reviews": self.recorded_reviews()}
        self.assertEqual(outcome, {"rejected": True, "event": "worker-output-rejected",
                                   "names_criterion_1": True, "reviews": []})

    def test_merge_ready_listing_only_index_zero_is_rejected_naming_missing_index_one(self):
        done = self.review_call({"verdict": "merge-ready", "findings": [],
                                 "verification": "re-ran the unit gate",
                                 "criteria": [{"index": 0, "result": "pass"}]})
        event = self.last_event()
        error = (event.get("detail") or {}).get("error", "")
        outcome = {"rejected": done.returncode != 0, "event": event["type"],
                   "names_missing_index_1": "missing" in error and "index 1" in error,
                   "reviews": self.recorded_reviews()}
        self.assertEqual(outcome, {"rejected": True, "event": "worker-output-rejected",
                                   "names_missing_index_1": True, "reviews": []})

    def test_merge_ready_listing_index_zero_twice_is_rejected_naming_repeated_index_zero(self):
        done = self.review_call({"verdict": "merge-ready", "findings": [],
                                 "verification": "re-ran the unit gate",
                                 "criteria": [{"index": 0, "result": "pass"},
                                              {"index": 0, "result": "pass"}]})
        event = self.last_event()
        error = (event.get("detail") or {}).get("error", "")
        outcome = {"rejected": done.returncode != 0, "event": event["type"],
                   "names_repeated_index_0": "repeated" in error and "index 0" in error,
                   "reviews": self.recorded_reviews()}
        self.assertEqual(outcome, {"rejected": True, "event": "worker-output-rejected",
                                   "names_repeated_index_0": True, "reviews": []})

    def test_blocked_naming_an_index_outside_the_run_is_rejected_at_its_position(self):
        self.review_call({"verdict": "blocked", "findings": [],
                          "verification": "re-ran the unit gate",
                          "criteria": [{"index": 9, "result": "pass"},
                                       {"index": 1, "result": "fail"}]})
        event = self.last_event()
        error = (event.get("detail") or {}).get("error", "")
        self.assertIn("criteria[0]: index 9 is not a criterion of this run", error)


if __name__ == "__main__":
    unittest.main()
