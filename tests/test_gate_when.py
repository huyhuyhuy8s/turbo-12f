import json
import os
import subprocess
import tempfile
import unittest

from test_t12 import APP_V1, APP_V2, BIN, TddFlow, task_dict


class GateWhenCase(TddFlow, unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = os.path.join(self.tmp.name, "product")
        self.home = os.path.join(self.tmp.name, "state")
        os.makedirs(os.path.join(self.repo, "tests"))
        self.flow_write(self.repo, "app.py", APP_V1)
        self.flow_write(self.repo, "tests/__init__.py", "")
        for args in (("init", "-b", "main"), ("config", "user.email", "t@example.com"),
                     ("config", "user.name", "T"), ("add", "."), ("commit", "-m", "init")):
            subprocess.run(["git", *args], cwd=self.repo, check=True, capture_output=True)
        self.task_path = os.path.join(self.tmp.name, "task.json")

    def tearDown(self):
        self.tmp.cleanup()

    def t12(self, *args, expect=0, extra_env=None):
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        result = subprocess.run([BIN, *args], capture_output=True, text=True, timeout=120, env=env)
        self.assertEqual(result.returncode, expect,
                         f"t12 {' '.join(args)} -> {result.returncode}: {result.stdout} {result.stderr}")
        return result

    def prepare(self, gate):
        with open(self.task_path, "w") as fh:
            json.dump(task_dict(gates=[gate]), fh)
        self.t12("init", "--repo", self.repo, "--home", self.home)
        self.t12("prepare", "--home", self.home, "--task", self.task_path)

    def passing_gate(self, **extra):
        return {"name": "unit", "command": ["python3", "-c", "pass"], **extra}

    def test_gate_whose_globs_miss_the_diff_is_skipped_without_running(self):
        self.prepare(self.passing_gate(when={"files": ["src/**"]}))
        self.run_tdd(files={"app.py": APP_V2, "docs/a.md": "notes\n"})
        self.t12("gate", "--home", self.home, "--task-id", "demo-1")
        gate = self.flow_record()["gates"][0]
        self.assertEqual((gate["status"], gate["observation"]), ("skipped", None))

    def test_skipping_a_gate_logs_gate_skipped_naming_the_gate(self):
        self.prepare(self.passing_gate(when={"files": ["src/**"]}))
        self.run_tdd(files={"app.py": APP_V2, "docs/a.md": "notes\n"})
        self.t12("gate", "--home", self.home, "--task-id", "demo-1")
        path = os.path.join(self.home, "runs", "demo-1.events.jsonl")
        with open(path) as fh:
            events = [json.loads(line) for line in fh if line.strip()]
        skipped = [e["detail"].get("gate") for e in events if e["type"] == "gate-skipped"]
        self.assertEqual(skipped, ["unit"])


if __name__ == "__main__":
    unittest.main()
