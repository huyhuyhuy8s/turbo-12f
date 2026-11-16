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


if __name__ == "__main__":
    unittest.main()
