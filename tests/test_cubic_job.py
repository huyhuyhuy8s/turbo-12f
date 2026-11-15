import importlib.util
import json
import os
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_cubic():
    spec = importlib.util.spec_from_file_location(
        "cubic_adapter", os.path.join(ROOT, "adapters", "cubic.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CubicJobTest(unittest.TestCase):
    def test_commit_equal_to_the_recorded_base_writes_no_findings_and_calls_no_process(self):
        job = {"name": "cubic", "adapter": "cubic", "config": {"since": "abc"}}
        calls = []

        def fake(argv, cwd, env):
            calls.append(argv)
            return 0, "", ""

        with tempfile.TemporaryDirectory() as tmp:
            state_dir = os.path.join(tmp, "jobs", "cubic")
            os.makedirs(state_dir)
            with open(os.path.join(state_dir, "cubic-last.json"), "w") as handle:
                json.dump({"commit": "abc"}, handle)
            out = os.path.join(tmp, "findings.json")
            argv = ["job", "--job", json.dumps(job), "--repo", "/repo", "--commit", "abc",
                    "--home", tmp, "--out", out]
            load_cubic().main(argv, run=fake)
            written = None
            if os.path.exists(out):
                with open(out) as handle:
                    written = json.load(handle)
        self.assertEqual((written, calls), ({"findings": []}, []))

    def test_one_cubic_p1_issue_becomes_one_finding_titled_with_its_priority(self):
        job = {"name": "cubic", "adapter": "cubic", "config": {"since": "abc"}}
        report = {"issues": [{"priority": "P1", "file": "src/a.py", "line": 3,
                              "title": "Null deref", "description": "x is None"}]}

        def fake(argv, cwd, env):
            if argv[0] == "git":
                return 0, "", ""
            return 1, json.dumps(report), ""

        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "findings.json")
            argv = ["job", "--job", json.dumps(job), "--repo", "/repo", "--commit", "def",
                    "--home", tmp, "--out", out]
            load_cubic().main(argv, run=fake)
            titles = None
            if os.path.exists(out):
                with open(out) as handle:
                    titles = [f["title"] for f in json.load(handle)["findings"]]
        self.assertEqual(titles, ["[P1] Null deref"])

    def test_one_cubic_p1_issue_detail_starts_with_its_file_and_line(self):
        job = {"name": "cubic", "adapter": "cubic", "config": {"since": "abc"}}
        report = {"issues": [{"priority": "P1", "file": "src/a.py", "line": 3,
                              "title": "Null deref", "description": "x is None"}]}

        def fake(argv, cwd, env):
            if argv[0] == "git":
                return 0, "", ""
            return 1, json.dumps(report), ""

        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "findings.json")
            argv = ["job", "--job", json.dumps(job), "--repo", "/repo", "--commit", "def",
                    "--home", tmp, "--out", out]
            load_cubic().main(argv, run=fake)
            heads = None
            if os.path.exists(out):
                with open(out) as handle:
                    heads = [f["detail"][:10] for f in json.load(handle)["findings"]]
        self.assertEqual(heads, ["src/a.py:3"])


if __name__ == "__main__":
    unittest.main()
