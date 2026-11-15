import json
import os
import subprocess
import tempfile
import unittest

from tests.test_t12 import APP_V1, BIN, TddFlow, task_dict


class TestListCoversCase(TddFlow, unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        repo = os.path.join(self.tmp.name, "product")
        self.home = os.path.join(self.tmp.name, "state")
        os.makedirs(os.path.join(repo, "tests"))
        with open(os.path.join(repo, "app.py"), "w") as fh:
            fh.write(APP_V1)
        open(os.path.join(repo, "tests", "__init__.py"), "w").close()
        for args in (["init", "-b", "main"],
                     ["config", "user.email", "test@example.com"],
                     ["config", "user.name", "Test"],
                     ["add", "."], ["commit", "-m", "init"]):
            subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
        task_path = os.path.join(self.tmp.name, "task.json")
        with open(task_path, "w") as fh:
            json.dump(task_dict(), fh)
        self.t12("init", "--repo", repo, "--home", self.home)
        self.t12("prepare", "--home", self.home, "--task", task_path)

    def t12(self, *args, expect=0):
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        result = subprocess.run([BIN, *args], capture_output=True, text=True, timeout=120, env=env)
        self.assertEqual(result.returncode, expect, f"t12 {args} -> {result.stdout} {result.stderr}")
        return result

    def item(self, text, criteria):
        return dict(self.DEFAULT_ITEM, text=text, criteria=criteria)

    def test_the_analyzer_is_refused_a_list_missing_a_criterion(self):
        result = self.analyze(items=[self.item("named greeting", [0])], expect=1)
        self.assertIn("[1]", result.stderr)
        self.assertIn("covered by no item", result.stderr)
        record = self.flow_record()
        self.assertEqual(record["phase"], "ready")
        self.assertEqual(record["tdd"]["items"], [])

    def test_the_analyzer_is_refused_an_unknown_criterion(self):
        result = self.analyze(items=[self.item("everything", [0, 1, 7])], expect=1)
        self.assertIn("unknown criterion 7", result.stderr)
        self.assertEqual(self.flow_phase(), "ready")

    def test_an_item_with_no_text_is_refused(self):
        result = self.analyze(items=[self.item("   ", [0, 1])], expect=1)
        self.assertIn("non-empty string", result.stderr)

    def test_the_list_is_recorded_with_the_criteria_each_item_covers(self):
        self.analyze(items=[self.item("named greeting", [0]), self.item("default greeting", [1, 0])])
        items = self.flow_record()["tdd"]["items"]
        self.assertEqual([(i["id"], i["criteria"], i["status"]) for i in items],
                         [("T1", [0], "todo"), ("T2", [1, 0], "todo")])
        self.assertEqual(self.flow_record()["tdd"]["step"], "red")
        self.assertEqual(self.flow_phase(), "tdd")


if __name__ == "__main__":
    unittest.main()
