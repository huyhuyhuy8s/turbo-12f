import importlib.util
import json
import os
import tempfile
import unittest
from unittest import mock

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

    def test_without_an_injected_run_the_real_processes_on_path_are_started(self):
        job = {"name": "cubic", "adapter": "cubic", "config": {"since": "abc"}}
        report = {"issues": [{"priority": "P1", "file": "src/a.py", "line": 3,
                              "title": "Null deref", "description": "x is None"}]}
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = os.path.join(tmp, "bin")
            os.makedirs(bin_dir)
            repo = os.path.join(tmp, "repo")
            os.makedirs(repo)
            for name, body in (("git", "exit 0"),
                               ("cubic", "echo '" + json.dumps(report) + "'\nexit 1")):
                path = os.path.join(bin_dir, name)
                with open(path, "w") as handle:
                    handle.write("#!/bin/sh\n" + body + "\n")
                os.chmod(path, 0o755)
            out = os.path.join(tmp, "findings.json")
            argv = ["job", "--job", json.dumps(job), "--repo", repo, "--commit", "def",
                    "--home", tmp, "--out", out]
            with mock.patch.dict(os.environ, {"PATH": bin_dir + os.pathsep + os.environ["PATH"]}):
                code = load_cubic().main(argv)
            titles = None
            if os.path.exists(out):
                with open(out) as handle:
                    titles = [f["title"] for f in json.load(handle)["findings"]]
        self.assertEqual((code, titles), (0, ["[P1] Null deref"]))

    def test_base_branch_is_deleted_after_the_cubic_review(self):
        job = {"name": "cubic", "adapter": "cubic", "config": {"since": "abc"}}
        report = {"issues": [{"priority": "P1", "file": "src/a.py", "line": 3,
                              "title": "Null deref", "description": "x is None"}]}
        calls = []

        def fake(argv, cwd, env):
            calls.append(argv)
            if argv[0] == "git":
                return 0, "", ""
            return 1, json.dumps(report), ""

        delete = ["git", "branch", "-D", "t12-job/cubic-base"]
        review = ["cubic", "review", "--base", "t12-job/cubic-base", "--json"]
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "findings.json")
            argv = ["job", "--job", json.dumps(job), "--repo", "/repo", "--commit", "def",
                    "--home", tmp, "--out", out]
            load_cubic().main(argv, run=fake)
        relevant = [c for c in calls if c in (delete, review)]
        self.assertEqual(relevant, [review, delete])

    def test_detached_checkout_is_removed_after_the_cubic_review(self):
        job = {"name": "cubic", "adapter": "cubic", "config": {"since": "abc"}}
        report = {"issues": [{"priority": "P1", "file": "src/a.py", "line": 3,
                              "title": "Null deref", "description": "x is None"}]}
        calls = []

        def fake(argv, cwd, env):
            calls.append((argv, cwd))
            if argv[0] == "git":
                return 0, "", ""
            return 1, json.dumps(report), ""

        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "findings.json")
            argv = ["job", "--job", json.dumps(job), "--repo", "/repo", "--commit", "def",
                    "--home", tmp, "--out", out]
            load_cubic().main(argv, run=fake)
        review_cwd = [cwd for a, cwd in calls if a[0] == "cubic"][0]
        after_review = calls[[a[0] for a, _ in calls].index("cubic") + 1:]
        removals = [a for a, _ in after_review if a[:4] == ["git", "worktree", "remove", "--force"]]
        self.assertEqual(removals, [["git", "worktree", "remove", "--force", review_cwd]])

    def test_cubic_exit_two_fails_the_run_and_records_no_commit(self):
        job = {"name": "cubic", "adapter": "cubic", "config": {"since": "abc"}}

        def fake(argv, cwd, env):
            if argv[0] == "git":
                return 0, "", ""
            return 2, json.dumps({"issues": []}), "boom"

        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "findings.json")
            argv = ["job", "--job", json.dumps(job), "--repo", "/repo", "--commit", "def",
                    "--home", tmp, "--out", out]
            code = load_cubic().main(argv, run=fake)
            recorded = os.path.exists(os.path.join(tmp, "jobs", "cubic", "cubic-last.json"))
        self.assertEqual((code != 0, recorded), (True, False))

    def test_failed_git_branch_fails_the_run_without_cubic_and_records_no_commit(self):
        job = {"name": "cubic", "adapter": "cubic", "config": {"since": "abc"}}
        calls = []

        def fake(argv, cwd, env):
            calls.append(argv)
            if argv[:2] == ["git", "branch"]:
                return 128, "", "fatal"
            if argv[0] == "git":
                return 0, "", ""
            return 1, json.dumps({"issues": []}), ""

        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "findings.json")
            argv = ["job", "--job", json.dumps(job), "--repo", "/repo", "--commit", "def",
                    "--home", tmp, "--out", out]
            code = load_cubic().main(argv, run=fake)
            recorded = os.path.exists(os.path.join(tmp, "jobs", "cubic", "cubic-last.json"))
        cubic_called = any(c[0] == "cubic" for c in calls)
        self.assertEqual((code != 0, cubic_called, recorded), (True, False, False))

    def test_failed_worktree_add_fails_the_run_without_branch_or_cubic_and_records_no_commit(self):
        job = {"name": "cubic", "adapter": "cubic", "config": {"since": "abc"}}
        calls = []

        def fake(argv, cwd, env):
            calls.append(argv)
            if argv[:3] == ["git", "worktree", "add"]:
                return 128, "", "fatal"
            if argv[0] == "git":
                return 0, "", ""
            return 1, json.dumps({"issues": []}), ""

        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "findings.json")
            argv = ["job", "--job", json.dumps(job), "--repo", "/repo", "--commit", "def",
                    "--home", tmp, "--out", out]
            code = load_cubic().main(argv, run=fake)
            recorded = os.path.exists(os.path.join(tmp, "jobs", "cubic", "cubic-last.json"))
        later = [c for c in calls if c[:2] == ["git", "branch"] or c[0] == "cubic"]
        self.assertEqual((code != 0, later, recorded), (True, [], False))

    def test_failed_cubic_review_still_deletes_the_base_branch_and_removes_the_checkout(self):
        job = {"name": "cubic", "adapter": "cubic", "config": {"since": "abc"}}
        calls = []

        def fake(argv, cwd, env):
            calls.append((argv, cwd))
            if argv[0] == "git":
                return 0, "", ""
            return 2, "", "boom"

        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "findings.json")
            argv = ["job", "--job", json.dumps(job), "--repo", "/repo", "--commit", "def",
                    "--home", tmp, "--out", out]
            load_cubic().main(argv, run=fake)
        review_cwd = [cwd for a, cwd in calls if a[0] == "cubic"][0]
        cleanup = [a for a, _ in calls if a[:2] in (["git", "branch"], ["git", "worktree"]) and
                   (a[2:3] == ["-D"] or a[2:3] == ["remove"])]
        self.assertEqual(cleanup, [["git", "branch", "-D", "t12-job/cubic-base"],
                                   ["git", "worktree", "remove", "--force", review_cwd]])

    def test_failed_git_branch_still_removes_the_detached_checkout(self):
        job = {"name": "cubic", "adapter": "cubic", "config": {"since": "abc"}}
        calls = []

        def fake(argv, cwd, env):
            calls.append((argv, cwd))
            if argv[:2] == ["git", "branch"]:
                return 128, "", "fatal"
            return 0, "", ""

        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "findings.json")
            argv = ["job", "--job", json.dumps(job), "--repo", "/repo", "--commit", "def",
                    "--home", tmp, "--out", out]
            load_cubic().main(argv, run=fake)
        checkout = [a[4] for a, _ in calls if a[:3] == ["git", "worktree", "add"]][0]
        removals = [a for a, _ in calls if a[:3] == ["git", "worktree", "remove"]]
        self.assertEqual(removals, [["git", "worktree", "remove", "--force", checkout]])

    def test_cubic_output_that_is_not_json_fails_the_run_and_records_no_commit(self):
        job = {"name": "cubic", "adapter": "cubic", "config": {"since": "abc"}}

        def fake(argv, cwd, env):
            if argv[0] == "git":
                return 0, "", ""
            return 0, "not json at all", ""

        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "findings.json")
            argv = ["job", "--job", json.dumps(job), "--repo", "/repo", "--commit", "def",
                    "--home", tmp, "--out", out]
            try:
                code = load_cubic().main(argv, run=fake)
            except Exception as error:
                code = repr(error)
            recorded = os.path.exists(os.path.join(tmp, "jobs", "cubic", "cubic-last.json"))
        failed = isinstance(code, int) and code != 0
        self.assertEqual((failed, recorded), (True, False))

    def test_cubic_json_without_an_issues_list_fails_the_run_and_records_no_commit(self):
        job = {"name": "cubic", "adapter": "cubic", "config": {"since": "abc"}}

        def fake(argv, cwd, env):
            if argv[0] == "git":
                return 0, "", ""
            return 0, '{"error": "no review"}', ""

        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "findings.json")
            argv = ["job", "--job", json.dumps(job), "--repo", "/repo", "--commit", "def",
                    "--home", tmp, "--out", out]
            try:
                code = load_cubic().main(argv, run=fake)
            except Exception as error:
                code = repr(error)
            recorded = os.path.exists(os.path.join(tmp, "jobs", "cubic", "cubic-last.json"))
        failed = isinstance(code, int) and code != 0
        self.assertEqual((failed, recorded), (True, False))

    def test_cubic_call_raising_oserror_still_cleans_up_the_branch_and_the_checkout(self):
        job = {"name": "cubic", "adapter": "cubic", "config": {"since": "abc"}}
        calls = []

        def fake(argv, cwd, env):
            calls.append(argv)
            if argv[0] == "git":
                return 0, "", ""
            raise OSError("cubic missing")

        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "findings.json")
            argv = ["job", "--job", json.dumps(job), "--repo", "/repo", "--commit", "def",
                    "--home", tmp, "--out", out]
            try:
                load_cubic().main(argv, run=fake)
            except OSError:
                pass
        checkout = [a[4] for a in calls if a[:3] == ["git", "worktree", "add"]][0]
        after = calls[[a[0] for a in calls].index("cubic") + 1:]
        self.assertEqual(after, [["git", "branch", "-D", "t12-job/cubic-base"],
                                 ["git", "worktree", "remove", "--force", checkout]])


if __name__ == "__main__":
    unittest.main()
