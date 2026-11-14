"""gh-46, ported to the tdd lane: after a changes-requested review the run
goes back to the analyzer with the review's findings, a second cycle of
red, green and refactor answers them, and finishing without a new commit is
refused instead of looping review forever."""

import json
import os
import subprocess
import tempfile
import unittest

from tests.test_t12 import APP_V1, BIN, TddFlow, task_dict

FINDING = "greet('') returns 'Hello, !' instead of falling back to the default name"
FINDINGS = [{"id": "F1", "class": "Contract", "severity": "Major", "where": "app.py:2",
             "finding": FINDING, "owner": "green"}]
EMPTY_TEST = """import unittest
from app import greet


class TestGreetEmpty(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(greet(""), "Hello, world!")
"""
APP_V3 = 'def greet(name="world"):\n    return f"Hello, {name or \'world\'}!"\n'


class ChangesRequestedLoop(TddFlow, unittest.TestCase):
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
        self.worktree = os.path.join(self.home, "worktrees", "demo-1")
        self.run_to_review()
        self.commit = self.git(self.worktree, "rev-parse", "HEAD")
        self.call("reviewer", "review", {"verdict": "blocked", "findings": FINDINGS,
                                         "verification": "re-ran the unit gate"})

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

    def status(self):
        out = self.t12("status", "--home", self.home, "--task-id", "demo-1").stdout
        return json.loads(out)

    def context(self, role):
        out = self.t12("context", "--home", self.home, "--task-id", "demo-1",
                       "--role", role, "--phase", "tdd").stdout
        return json.loads(out.rsplit("--- context (JSON) ---\n", 1)[1])

    def test_the_run_goes_back_to_the_analyzer_in_review_fix_mode(self):
        status = self.status()
        self.assertEqual(status["phase"], "tdd")
        self.assertEqual((status["tdd"]["step"], status["tdd"]["mode"]), ("analyzer", "review-fix"))
        self.assertEqual(status["budgets"]["repairs_used"], 1)
        self.assertEqual(status["candidate"], self.commit)

    def test_analyzer_context_carries_the_changes_requested_review(self):
        context = self.context("analyzer")
        self.assertEqual(context["mode"], "review-fix")
        self.assertEqual([f["id"] for f in context["findings"]], ["F1"])
        self.assertIn(FINDING, json.dumps(context["findings"]))
        self.assertEqual([i["status"] for i in context["items"]], ["done"])

    def test_the_findings_never_reach_green_before_the_analyzer_has_answered(self):
        out = self.t12("context", "--home", self.home, "--task-id", "demo-1",
                       "--role", "green", "--phase", "tdd").stdout
        context = json.loads(out.rsplit("--- context (JSON) ---\n", 1)[1])
        self.assertNotIn(FINDING, json.dumps(context))
        self.assertIsNone(context["findings"])

    def test_finishing_without_a_new_commit_is_refused(self):
        self.analyze(items=[], directives=["F1: tidy greet"])
        self.refactor()
        self.no_cheat()
        before = self.status()["candidate"]
        result = self.tdd_step(expect=1)
        message = result.stderr.lower()
        self.assertIn("changes", message)
        self.assertIn("no new commit", message)
        after = self.status()
        self.assertEqual(after["candidate"], before)
        self.assertEqual(after["phase"], "tdd")
        self.assertEqual(after["tdd"]["step"], "green")

    def test_a_second_cycle_answers_the_finding_and_reaches_local_verified(self):
        self.analyze(items=[{
            "text": "greet('') falls back to the default name", "kind": "edge",
            "source": f"F1: {FINDING}", "needed": "greet that formats an empty name",
            "criteria": [1]}])
        self.assertEqual(self.status()["tdd"]["step"], "red")
        self.red(item_id="T2", test_file="tests/test_greet_empty.py", text=EMPTY_TEST,
                 line=7, name="test_empty")
        self.green(app=APP_V3)
        self.refactor()
        self.no_cheat()
        self.tdd_step()
        status = self.status()
        self.assertEqual(status["phase"], "gates")
        self.assertEqual(status["tdd"]["cycle"], 2)
        self.assertNotEqual(status["candidate"], self.commit)
        record = self.flow_record()
        self.assertTrue(record["reviews"][0]["voided"])
        self.assertEqual(record["gates"][0]["status"], "pending")
        self.run_gates()
        self.call("reviewer", "review", {"verdict": "merge-ready", "findings": [],
                                         "verification": "F1 fixed; unit gate re-run: OK"})
        self.check()
        self.assertEqual(self.status()["phase"], "local-verified")
        self.t12("verify", "--home", self.home, "--task-id", "demo-1")
        reviews = self.flow_record()["reviews"]
        self.assertEqual([r["verdict"] for r in reviews], ["changes-requested", "approve"])
        self.assertEqual(reviews[1]["prior_review"], reviews[0]["report"]["name"])


if __name__ == "__main__":
    unittest.main()
