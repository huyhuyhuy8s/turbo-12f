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
        if expect is not None:
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

    def test_gate_whose_globs_match_the_diff_runs_its_command(self):
        self.prepare(self.passing_gate(when={"files": ["src/**"]}))
        self.run_tdd(files={"app.py": APP_V2, "src/a.py": "x = 1\n"})
        self.t12("gate", "--home", self.home, "--task-id", "demo-1")
        gate = self.flow_record()["gates"][0]
        self.assertEqual(gate["status"], "passed")

    def test_gate_without_when_runs_even_when_only_docs_change(self):
        self.prepare(self.passing_gate())
        self.run_tdd(files={"app.py": APP_V2, "docs/a.md": "notes\n"})
        self.t12("gate", "--home", self.home, "--task-id", "demo-1")
        gate = self.flow_record()["gates"][0]
        self.assertEqual(gate["status"], "passed")

    def test_skipping_the_last_pending_gate_moves_the_run_to_review(self):
        self.prepare(self.passing_gate(when={"files": ["src/**"]}))
        self.run_tdd(files={"app.py": APP_V2, "docs/a.md": "notes\n"})
        self.t12("gate", "--home", self.home, "--task-id", "demo-1")
        self.assertEqual(self.flow_record()["phase"], "review")

    def test_check_certifies_a_run_whose_gate_was_skipped_and_the_manifest_lists_it(self):
        self.prepare(self.passing_gate(when={"files": ["src/**"]}))
        self.run_tdd(files={"app.py": APP_V2, "docs/a.md": "notes\n"})
        self.t12("gate", "--home", self.home, "--task-id", "demo-1")
        self.do_review()
        checked = self.t12("check", "--home", self.home, "--task-id", "demo-1", expect=None)
        manifest = os.path.join(self.home, "evidence", "demo-1", "manifest.txt")
        text = ""
        if os.path.exists(manifest):
            with open(manifest) as fh:
                text = fh.read()
        self.assertEqual((self.flow_record()["phase"], "unit: skipped" in text),
                         ("local-verified", True), checked.stderr)

    def test_prepare_refuses_a_gate_whose_when_files_is_empty_and_names_the_gate(self):
        with open(self.task_path, "w") as fh:
            json.dump(task_dict(gates=[self.passing_gate(name="docs-lint", when={"files": []})]), fh)
        self.t12("init", "--repo", self.repo, "--home", self.home)
        refused = self.t12("prepare", "--home", self.home, "--task", self.task_path, expect=None)
        self.assertEqual((refused.returncode, "docs-lint" in refused.stderr), (1, True), refused.stderr)

    def test_prepare_refuses_a_gate_whose_when_has_a_key_other_than_files_and_names_the_gate(self):
        with open(self.task_path, "w") as fh:
            json.dump(task_dict(gates=[self.passing_gate(
                name="docs-lint", when={"files": ["src/**"], "branch": "x"})]), fh)
        self.t12("init", "--repo", self.repo, "--home", self.home)
        refused = self.t12("prepare", "--home", self.home, "--task", self.task_path, expect=None)
        self.assertEqual((refused.returncode, "docs-lint" in refused.stderr), (1, True), refused.stderr)

    def test_prepare_refuses_a_gate_with_a_key_other_than_name_command_and_when_and_names_the_gate(self):
        with open(self.task_path, "w") as fh:
            json.dump(task_dict(gates=[self.passing_gate(name="docs-lint", order=1)]), fh)
        self.t12("init", "--repo", self.repo, "--home", self.home)
        refused = self.t12("prepare", "--home", self.home, "--task", self.task_path, expect=None)
        self.assertEqual((refused.returncode, "docs-lint" in refused.stderr), (1, True), refused.stderr)

    def test_prepare_names_the_forbidden_flag_even_when_the_gate_also_has_an_unknown_key(self):
        with open(self.task_path, "w") as fh:
            json.dump(task_dict(gates=[{"name": "unit", "order": 1,
                                        "command": ["agy", "--dangerously-skip-permissions"]}]), fh)
        self.t12("init", "--repo", self.repo, "--home", self.home)
        refused = self.t12("prepare", "--home", self.home, "--task", self.task_path, expect=None)
        self.assertEqual((refused.returncode, "--dangerously-skip-permissions" in refused.stderr),
                         (1, True), refused.stderr)

    def test_prepare_refuses_a_gate_without_command_with_the_argv_message_and_no_traceback(self):
        with open(self.task_path, "w") as fh:
            json.dump(task_dict(gates=[{"name": "unit"}]), fh)
        self.t12("init", "--repo", self.repo, "--home", self.home)
        refused = self.t12("prepare", "--home", self.home, "--task", self.task_path, expect=None)
        self.assertEqual((refused.returncode, "must have a non-empty argv command" in refused.stderr,
                          "Traceback" in refused.stderr), (1, True, False), refused.stderr)

    def test_prepare_refuses_a_gate_whose_command_is_null_false_zero_or_empty_string(self):
        self.t12("init", "--repo", self.repo, "--home", self.home)
        outcomes = {}
        for value in (None, False, 0, ""):
            with open(self.task_path, "w") as fh:
                json.dump(task_dict(gates=[{"name": "unit", "command": value}]), fh)
            refused = self.t12("prepare", "--home", self.home, "--task", self.task_path, expect=None)
            outcomes[repr(value)] = (refused.returncode,
                                     "must have a non-empty argv command" in refused.stderr,
                                     "Traceback" in refused.stderr)
        expected = {repr(v): (1, True, False) for v in (None, False, 0, "")}
        self.assertEqual(outcomes, expected)

    def test_prepare_refuses_a_gate_whose_when_files_is_a_string_not_a_list_and_names_the_gate(self):
        with open(self.task_path, "w") as fh:
            json.dump(task_dict(gates=[self.passing_gate(name="docs-lint", when={"files": "src/**"})]), fh)
        self.t12("init", "--repo", self.repo, "--home", self.home)
        refused = self.t12("prepare", "--home", self.home, "--task", self.task_path, expect=None)
        self.assertEqual((refused.returncode, "docs-lint" in refused.stderr), (1, True), refused.stderr)

    def test_prepare_refuses_a_gate_whose_when_files_holds_a_non_string_glob_and_names_the_gate(self):
        with open(self.task_path, "w") as fh:
            json.dump(task_dict(gates=[self.passing_gate(name="docs-lint", when={"files": [1]})]), fh)
        self.t12("init", "--repo", self.repo, "--home", self.home)
        refused = self.t12("prepare", "--home", self.home, "--task", self.task_path, expect=None)
        self.assertEqual((refused.returncode, "docs-lint" in refused.stderr), (1, True), refused.stderr)

    def test_repair_pinning_a_new_candidate_resets_a_skipped_gate_and_checks_the_new_diff(self):
        self.prepare(self.passing_gate(when={"files": ["src/**"]}))
        self.run_tdd(files={"app.py": APP_V2, "docs/a.md": "notes\n"})
        self.t12("gate", "--home", self.home, "--task-id", "demo-1")
        self.do_review(verdict="changes-requested")
        self.analyze(items=[], directives=["F1: add the src module"])
        self.refactor(files={"app.py": APP_V2 + "# addressed review\n", "src/a.py": "x = 1\n"})
        self.no_cheat()
        self.tdd_step()
        after_pin = self.flow_record()["gates"][0]["status"]
        self.t12("gate", "--home", self.home, "--task-id", "demo-1", expect=None)
        after_gate = self.flow_record()["gates"][0]["status"]
        self.assertEqual((after_pin, after_gate), ("pending", "passed"))

    def test_prepare_refuses_a_gate_whose_when_is_not_an_object_without_a_traceback(self):
        self.t12("init", "--repo", self.repo, "--home", self.home)
        outcomes = {}
        for value in (False, 0, ["files"]):
            with open(self.task_path, "w") as fh:
                json.dump(task_dict(gates=[self.passing_gate(name="docs-lint", when=value)]), fh)
            refused = self.t12("prepare", "--home", self.home, "--task", self.task_path, expect=None)
            outcomes[repr(value)] = (refused.returncode, "docs-lint" in refused.stderr,
                                     "Traceback" in refused.stderr)
        expected = {repr(v): (1, True, False) for v in (False, 0, ["files"])}
        self.assertEqual(outcomes, expected)

    def test_prepare_refuses_a_gate_whose_command_is_not_a_list_of_strings_without_a_traceback(self):
        self.t12("init", "--repo", self.repo, "--home", self.home)
        outcomes = {}
        for value in (True, 5, "pytest", [1]):
            with open(self.task_path, "w") as fh:
                json.dump(task_dict(gates=[{"name": "unit", "command": value}]), fh)
            refused = self.t12("prepare", "--home", self.home, "--task", self.task_path, expect=None)
            outcomes[repr(value)] = (refused.returncode,
                                     "must have a non-empty argv command" in refused.stderr,
                                     "Traceback" in refused.stderr)
        expected = {repr(v): (1, True, False) for v in (True, 5, "pytest", [1])}
        self.assertEqual(outcomes, expected)

    def test_board_counts_a_skipped_gate_as_cleared_so_the_runner_sees_progress(self):
        with open(self.task_path, "w") as fh:
            json.dump(task_dict(gates=[
                self.passing_gate(name="src-unit", when={"files": ["src/**"]}),
                self.passing_gate(name="lint")]), fh)
        self.t12("init", "--repo", self.repo, "--home", self.home)
        self.t12("prepare", "--home", self.home, "--task", self.task_path)
        self.run_tdd(files={"app.py": APP_V2, "docs/a.md": "notes\n"})
        self.t12("gate", "--home", self.home, "--task-id", "demo-1")
        board = json.loads(self.t12("board", "--home", self.home, "--json").stdout)
        row = [r for r in board if r["task_id"] == "demo-1"][0]
        self.assertEqual((row["phase"], row["gates_passed"]), ("gates", 1))


if __name__ == "__main__":
    unittest.main()
