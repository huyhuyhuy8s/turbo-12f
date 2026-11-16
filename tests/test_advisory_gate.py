import json
import os
import subprocess
import tempfile
import unittest

from test_t12 import APP_V1, APP_V2, BIN, TddFlow, task_dict


class AdvisoryGateCase(TddFlow, unittest.TestCase):
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
        if expect is not None:
            self.assertEqual(result.returncode, expect,
                             f"t12 {' '.join(args)} -> {result.returncode}: {result.stdout} {result.stderr}")
        return result

    def prepare(self, gates):
        with open(self.task_path, "w") as fh:
            json.dump(task_dict(gates=gates), fh)
        self.t12("init", "--repo", self.repo, "--home", self.home)
        self.t12("prepare", "--home", self.home, "--task", self.task_path)

    def test_failed_advisory_gate_keeps_the_run_in_gates_with_the_next_gate_pending(self):
        self.prepare([
            {"name": "lint", "command": ["python3", "-c", "import sys; sys.exit(1)"],
             "advisory": True},
            {"name": "unit", "command": ["python3", "-c", "pass"]},
        ])
        self.run_tdd(files={"app.py": APP_V2})
        self.t12("gate", "--home", self.home, "--task-id", "demo-1", expect=None)
        record = self.flow_record()
        self.assertEqual(
            (record["gates"][0]["status"], record["phase"], record["gates"][1]["status"]),
            ("failed", "gates", "pending"))

    def test_failed_advisory_gate_leaves_the_repairs_used_unchanged(self):
        self.prepare([
            {"name": "lint", "command": ["python3", "-c", "import sys; sys.exit(1)"],
             "advisory": True},
            {"name": "unit", "command": ["python3", "-c", "pass"]},
        ])
        self.run_tdd(files={"app.py": APP_V2})
        before = self.flow_record()["budgets"]["repairs_used"]
        self.t12("gate", "--home", self.home, "--task-id", "demo-1", expect=None)
        self.assertEqual(self.flow_record()["budgets"]["repairs_used"], before)

    def test_failed_gate_without_advisory_sends_the_run_back_to_green_and_spends_a_repair(self):
        self.prepare([
            {"name": "unit", "command": ["python3", "-c", "import sys; sys.exit(1)"]},
        ])
        self.run_tdd(files={"app.py": APP_V2})
        before = self.flow_record()["budgets"]["repairs_used"]
        self.t12("gate", "--home", self.home, "--task-id", "demo-1", expect=None)
        record = self.flow_record()
        self.assertEqual(
            (record["phase"], record["tdd"]["step"], record["budgets"]["repairs_used"]),
            ("tdd", "green", before + 1))

    def test_prepare_refuses_a_string_advisory_value_and_names_the_gate(self):
        with open(self.task_path, "w") as fh:
            json.dump(task_dict(gates=[
                {"name": "lint-soft", "command": ["python3", "-c", "pass"], "advisory": "yes"},
            ]), fh)
        self.t12("init", "--repo", self.repo, "--home", self.home)
        result = self.t12("prepare", "--home", self.home, "--task", self.task_path, expect=None)
        self.assertEqual(
            (result.returncode != 0, "lint-soft" in result.stdout + result.stderr),
            (True, True), f"{result.returncode}: {result.stdout} {result.stderr}")

    def test_reviewer_context_lists_a_failed_advisory_gate_with_exit_code_and_output(self):
        self.prepare([
            {"name": "unit", "command": ["python3", "-c", "pass"]},
            {"name": "lint-soft",
             "command": ["python3", "-c", "import sys; print('style-marker-xyz'); sys.exit(3)"],
             "advisory": True},
        ])
        self.run_tdd(files={"app.py": APP_V2})
        self.t12("gate", "--home", self.home, "--task-id", "demo-1")
        self.t12("gate", "--home", self.home, "--task-id", "demo-1", expect=None)
        result = self.t12("context", "--home", self.home, "--task-id", "demo-1",
                          "--role", "reviewer", "--phase", "review")
        context = json.loads(result.stdout.rsplit("--- context (JSON) ---\n", 1)[1])
        listed = context.get("advisory_gates", [])
        self.assertEqual(
            ([g.get("name") for g in listed], [g.get("exit_code") for g in listed],
             "style-marker-xyz" in json.dumps(listed)),
            (["lint-soft"], [3], True), f"advisory_gates: {listed!r}")

    def review_report(self):
        path = os.path.join(self.tmp.name, "review.md")
        with open(path, "w") as fh:
            fh.write("Verdict: approve\n\n## Findings\n\nNone. Hypotheses checked "
                     "against the diff; verification re-run.\n")
        return path

    def test_check_certifies_local_verified_despite_a_failed_advisory_gate(self):
        self.prepare([
            {"name": "unit", "command": ["python3", "-c", "pass"]},
            {"name": "lint-soft", "command": ["python3", "-c", "import sys; sys.exit(3)"],
             "advisory": True},
        ])
        self.run_tdd(files={"app.py": APP_V2})
        self.t12("gate", "--home", self.home, "--task-id", "demo-1")
        self.t12("gate", "--home", self.home, "--task-id", "demo-1", expect=None)
        self.t12("review", "--home", self.home, "--task-id", "demo-1", "--role", "reviewer",
                 "--session", "sess-reviewer", "--verdict", "approve",
                 "--report", self.review_report(), expect=None)
        self.t12("check", "--home", self.home, "--task-id", "demo-1", expect=None)
        self.assertEqual(self.flow_record()["phase"], "local-verified")


if __name__ == "__main__":
    unittest.main()
