import importlib.util
import json
import os
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_cubic():
    spec = importlib.util.spec_from_file_location(
        "cubic_adapter_failures", os.path.join(ROOT, "adapters", "cubic.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CubicJobFailuresTest(unittest.TestCase):
    def test_no_recorded_commit_and_no_since_exits_1_without_calling_run(self):
        job = {"name": "cubic", "adapter": "cubic", "config": {}}
        calls = []

        def fake(argv, cwd, env):
            calls.append(argv)
            return 0, "", ""

        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "findings.json")
            argv = ["job", "--job", json.dumps(job), "--repo", "/repo", "--commit", "def",
                    "--home", tmp, "--out", out]
            code = load_cubic().main(argv, run=fake)
        self.assertEqual((code, calls), (1, []))

    def test_cubic_exit_2_exits_1_without_out_file_or_record_update(self):
        job = {"name": "cubic", "adapter": "cubic", "config": {}}

        def fake(argv, cwd, env):
            if argv[0] == "cubic":
                return 2, json.dumps({"issues": []}), ""
            return 0, "", ""

        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "findings.json")
            last = os.path.join(tmp, "jobs", "cubic", "cubic-last.json")
            os.makedirs(os.path.dirname(last))
            with open(last, "w") as handle:
                json.dump({"commit": "abc"}, handle)
            argv = ["job", "--job", json.dumps(job), "--repo", "/repo", "--commit", "def",
                    "--home", tmp, "--out", out]
            code = load_cubic().main(argv, run=fake)
            with open(last) as handle:
                recorded = json.load(handle)
            self.assertEqual((code, os.path.exists(out), recorded), (1, False, {"commit": "abc"}))

    def test_cubic_non_json_output_exits_1_without_out_file_or_record_update(self):
        job = {"name": "cubic", "adapter": "cubic", "config": {}}

        def fake(argv, cwd, env):
            if argv[0] == "cubic":
                return 0, "Review complete, no issues", ""
            return 0, "", ""

        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "findings.json")
            last = os.path.join(tmp, "jobs", "cubic", "cubic-last.json")
            os.makedirs(os.path.dirname(last))
            with open(last, "w") as handle:
                json.dump({"commit": "abc"}, handle)
            argv = ["job", "--job", json.dumps(job), "--repo", "/repo", "--commit", "def",
                    "--home", tmp, "--out", out]
            code = load_cubic().main(argv, run=fake)
            with open(last) as handle:
                recorded = json.load(handle)
            self.assertEqual((code, os.path.exists(out), recorded), (1, False, {"commit": "abc"}))

    def test_cubic_error_field_with_empty_issues_exits_1_without_out_file_or_record_update(self):
        job = {"name": "cubic", "adapter": "cubic", "config": {}}

        def fake(argv, cwd, env):
            if argv[0] == "cubic":
                return 1, json.dumps({"issues": [], "error": "Review failed"}), ""
            return 0, "", ""

        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "findings.json")
            last = os.path.join(tmp, "jobs", "cubic", "cubic-last.json")
            os.makedirs(os.path.dirname(last))
            with open(last, "w") as handle:
                json.dump({"commit": "abc"}, handle)
            argv = ["job", "--job", json.dumps(job), "--repo", "/repo", "--commit", "def",
                    "--home", tmp, "--out", out]
            code = load_cubic().main(argv, run=fake)
            with open(last) as handle:
                recorded = json.load(handle)
            self.assertEqual((code, os.path.exists(out), recorded), (1, False, {"commit": "abc"}))


if __name__ == "__main__":
    unittest.main()
