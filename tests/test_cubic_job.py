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

    def test_base_branch_is_created_from_since_before_cubic_reviews_against_it(self):
        job = {"name": "cubic", "adapter": "cubic", "config": {"since": "abc"}}
        report = {"issues": [{"priority": "P1", "file": "src/a.py", "line": 3,
                              "title": "Null deref", "description": "x is None"}]}
        calls = []

        def fake(argv, cwd, env):
            calls.append(argv)
            if argv[0] == "git":
                return 0, "", ""
            return 1, json.dumps(report), ""

        branch = ["git", "branch", "t12-job/cubic-base", "abc"]
        review = ["cubic", "review", "--base", "t12-job/cubic-base", "--json"]
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "findings.json")
            argv = ["job", "--job", json.dumps(job), "--repo", "/repo", "--commit", "def",
                    "--home", tmp, "--out", out]
            load_cubic().main(argv, run=fake)
        relevant = [c for c in calls if c in (branch, review)]
        self.assertEqual(relevant, [branch, review])

    def test_cubic_review_runs_in_a_directory_other_than_the_repo(self):
        job = {"name": "cubic", "adapter": "cubic", "config": {"since": "abc"}}
        report = {"issues": [{"priority": "P1", "file": "src/a.py", "line": 3,
                              "title": "Null deref", "description": "x is None"}]}
        review_cwds = []

        def fake(argv, cwd, env):
            if argv[0] == "git":
                return 0, "", ""
            review_cwds.append(cwd)
            return 1, json.dumps(report), ""

        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "findings.json")
            argv = ["job", "--job", json.dumps(job), "--repo", "/repo", "--commit", "def",
                    "--home", tmp, "--out", out]
            load_cubic().main(argv, run=fake)
        self.assertNotEqual(review_cwds, ["/repo"])

    def test_second_run_on_a_new_commit_branches_from_the_commit_of_the_first_run(self):
        job = {"name": "cubic", "adapter": "cubic", "config": {"since": "abc"}}
        calls = []

        def fake(argv, cwd, env):
            calls.append(argv)
            if argv[0] == "git":
                return 0, "", ""
            return 0, json.dumps({"issues": []}), ""

        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "findings.json")
            for commit in ("first", "second"):
                argv = ["job", "--job", json.dumps(job), "--repo", "/repo", "--commit", commit,
                        "--home", tmp, "--out", out]
                load_cubic().main(argv, run=fake)
        branches = [c for c in calls if c[:3] == ["git", "branch", "t12-job/cubic-base"]]
        self.assertEqual(branches, [["git", "branch", "t12-job/cubic-base", "abc"],
                                    ["git", "branch", "t12-job/cubic-base", "first"]])

    def test_issues_with_the_same_file_and_title_share_one_fingerprint(self):
        job = {"name": "cubic", "adapter": "cubic", "config": {"since": "abc"}}
        report = {"issues": [
            {"priority": "P1", "file": "src/a.py", "line": 3,
             "title": "Null deref", "description": "x is None"},
            {"priority": "P1", "file": "src/a.py", "line": 40,
             "title": "Null deref", "description": "y is None"}]}

        def fake(argv, cwd, env):
            if argv[0] == "git":
                return 0, "", ""
            return 1, json.dumps(report), ""

        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "findings.json")
            argv = ["job", "--job", json.dumps(job), "--repo", "/repo", "--commit", "def",
                    "--home", tmp, "--out", out]
            load_cubic().main(argv, run=fake)
            distinct = None
            if os.path.exists(out):
                with open(out) as handle:
                    distinct = len({f["fingerprint"] for f in json.load(handle)["findings"]})
        self.assertEqual(distinct, 1)

    def test_issue_without_a_line_has_a_detail_headed_by_the_file_alone(self):
        job = {"name": "cubic", "adapter": "cubic", "config": {"since": "abc"}}
        report = {"issues": [{"priority": "P2", "file": "src/b.py",
                              "title": "Unused", "description": "y unused"}]}

        def fake(argv, cwd, env):
            if argv[0] == "git":
                return 0, "", ""
            return 1, json.dumps(report), ""

        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "findings.json")
            argv = ["job", "--job", json.dumps(job), "--repo", "/repo", "--commit", "def",
                    "--home", tmp, "--out", out]
            try:
                load_cubic().main(argv, run=fake)
            except KeyError:
                pass
            details = None
            if os.path.exists(out):
                with open(out) as handle:
                    details = [f["detail"] for f in json.load(handle)["findings"]]
        self.assertEqual(details, ["src/b.py\n\ny unused"])


if __name__ == "__main__":
    unittest.main()
