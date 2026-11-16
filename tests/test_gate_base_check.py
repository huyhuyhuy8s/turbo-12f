import json
import os
import subprocess
import tempfile
import unittest

from test_t12 import APP_V1, APP_V2, BIN, TddFlow, task_dict

ALWAYS_FAIL = "import sys\nsys.exit(1)\n"


class GateBaseCheckCase(TddFlow, unittest.TestCase):
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

    def test_a_base_check_gate_failing_on_candidate_and_base_pauses_as_known_red_naming_the_gate(self):
        self.prepare({"name": "unit", "command": ["python3", "-c", ALWAYS_FAIL], "base_check": True})
        self.run_tdd(files={"app.py": APP_V2})
        self.t12("gate", "--home", self.home, "--task-id", "demo-1", expect=None)
        escalations = self.flow_record().get("escalations") or []
        last = escalations[-1] if escalations else {"kind": None, "question": ""}
        self.assertEqual((last["kind"], "unit" in last["question"]), ("known-red", True))


if __name__ == "__main__":
    unittest.main()
