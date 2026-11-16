import json
import os
import subprocess
import tempfile
import unittest

from test_t12 import APP_V1, APP_V2, BIN, TddFlow, task_dict

# Counts its runs in $HOME, which persists across verification runs of a task;
# fails on the first run and passes on every later one.
FAIL_THEN_PASS = (
    "import os, sys\n"
    "p = os.path.join(os.environ['HOME'], 'runs')\n"
    "n = int(open(p).read()) if os.path.exists(p) else 0\n"
    "open(p, 'w').write(str(n + 1))\n"
    "sys.exit(1 if n == 0 else 0)\n"
)


class GateRerunsCase(TddFlow, unittest.TestCase):
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

    def t12(self, *args, expect=0):
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        result = subprocess.run([BIN, *args], capture_output=True, text=True, timeout=120, env=env)
        if expect is not None:
            self.assertEqual(result.returncode, expect,
                             f"t12 {' '.join(args)} -> {result.returncode}: {result.stdout} {result.stderr}")
        return result

    def prepare(self, gate):
        with open(self.task_path, "w") as fh:
            json.dump(task_dict(gates=[gate]), fh)
        self.t12("init", "--repo", self.repo, "--home", self.home)
        self.t12("prepare", "--home", self.home, "--task", self.task_path)

    def test_a_gate_with_one_rerun_that_fails_then_passes_ends_passed(self):
        self.prepare({"name": "unit", "command": ["python3", "-c", FAIL_THEN_PASS], "reruns": 1})
        self.run_tdd(files={"app.py": APP_V2})
        self.t12("gate", "--home", self.home, "--task-id", "demo-1", expect=None)
        gate = self.flow_record()["gates"][0]
        self.assertEqual(gate["status"], "passed")

    def test_a_gate_that_passes_on_a_rerun_logs_gate_flaky_with_the_failed_run_count(self):
        self.prepare({"name": "unit", "command": ["python3", "-c", FAIL_THEN_PASS], "reruns": 1})
        self.run_tdd(files={"app.py": APP_V2})
        self.t12("gate", "--home", self.home, "--task-id", "demo-1", expect=None)
        with open(os.path.join(self.home, "runs", "demo-1.events.jsonl")) as fh:
            events = [json.loads(line) for line in fh if line.strip()]
        flaky = [e["detail"].get("failed_runs") for e in events if e["type"] == "gate-flaky"]
        self.assertEqual(flaky, [1])

    def test_a_gate_that_passes_on_a_rerun_spends_no_repair(self):
        self.prepare({"name": "unit", "command": ["python3", "-c", FAIL_THEN_PASS], "reruns": 1})
        self.run_tdd(files={"app.py": APP_V2})
        before = self.flow_record()["budgets"]["repairs_used"]
        self.t12("gate", "--home", self.home, "--task-id", "demo-1", expect=None)
        self.assertEqual(self.flow_record()["budgets"]["repairs_used"], before)


if __name__ == "__main__":
    unittest.main()
