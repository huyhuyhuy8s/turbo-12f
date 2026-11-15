import json
import os
import subprocess
import tempfile
import time
import unittest
from unittest import mock

from t12 import cli, model
from tests.test_t12 import APP_V1, BIN, TddFlow, task_dict


class PausedTimeCase(TddFlow, unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        repo = os.path.join(self.tmp.name, "product")
        self.home = os.path.join(self.tmp.name, "state")
        os.makedirs(os.path.join(repo, "tests"))
        for args in (("init", "-b", "main"), ("config", "user.email", "test@example.com"),
                     ("config", "user.name", "Test")):
            self.git(repo, *args)
        for rel, text in (("app.py", APP_V1), ("tests/__init__.py", "")):
            with open(os.path.join(repo, rel), "w") as fh:
                fh.write(text)
        self.git(repo, "add", ".")
        self.git(repo, "commit", "-m", "init")
        task_path = os.path.join(self.tmp.name, "task.json")
        with open(task_path, "w") as fh:
            json.dump(task_dict(), fh)
        self.t12("init", "--repo", repo, "--home", self.home)
        self.t12("prepare", "--home", self.home, "--task", task_path)
        self.worktree = os.path.join(self.home, "worktrees", "demo-1")
        self.analyze()
        self.red()

    def git(self, cwd, *args):
        result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)

    def t12(self, *args, expect=0):
        result = subprocess.run([BIN, *args], capture_output=True, text=True, timeout=120,
                                env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
        self.assertEqual(result.returncode, expect, f"t12 {' '.join(args)}: {result.stderr}")
        return result

    def away(self, command):
        now = time.time()
        with mock.patch("time.time", return_value=now):
            self.assertEqual(cli.main(command + ["--home", self.home, "--task-id", "demo-1"]), 0)
            self.assertEqual(self.flow_phase(), "paused")
        with mock.patch("time.time", return_value=now + 100000):
            args = ["resume", "--home", self.home, "--task-id", "demo-1"]
            if model.open_escalation(model.load_run(self.home, "demo-1")):
                args += ["--answer", "yes"]
            self.assertEqual(cli.main(args), 0)
        return now + 100010

    def test_paused_time_does_not_count_against_the_time_budget(self):
        later = self.away(["pause", "--reason", "away"])
        with mock.patch("time.time", return_value=later):
            run = model.load_run(self.home, "demo-1")
            model.check_budgets(run)
            self.assertGreater(model.time_left_s(run), 400)

    def test_time_waiting_on_an_escalation_does_not_count_either(self):
        later = self.away(["escalate", "--question", "is this the right approach?"])
        with mock.patch("time.time", return_value=later):
            run = model.load_run(self.home, "demo-1")
            model.check_budgets(run)
            self.assertGreater(model.time_left_s(run), 400)

    def test_time_used_outside_the_pause_still_counts(self):
        later = self.away(["pause", "--reason", "away"])
        run = model.load_run(self.home, "demo-1")
        with mock.patch("time.time", return_value=later + run["budgets"]["time_limit_s"]):
            with self.assertRaises(model.BudgetError):
                model.check_budgets(run)


if __name__ == "__main__":
    unittest.main()
