import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JOB = os.path.join(ROOT, "bin", "t12-job")


def git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def make_home(tmp, jobs):
    repo = os.path.join(tmp, "product")
    os.makedirs(os.path.join(repo, ".t12"))
    with open(os.path.join(repo, ".t12", "project.json"), "w") as fh:
        json.dump({"version": 1, "jobs": jobs}, fh)
    for args in (["init", "-q", "-b", "main"], ["config", "user.email", "t@example.com"],
                 ["config", "user.name", "T"], ["add", "."], ["commit", "-qm", "profile"]):
        git(repo, *args)
    home = os.path.join(tmp, "home")
    os.makedirs(home)
    with open(os.path.join(home, "config.json"), "w") as fh:
        json.dump({"version": 1, "repo": repo, "trunk": "main"}, fh)
    return home


def findings_of(path):
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)["findings"]


class CommandJobTest(unittest.TestCase):
    def test_a_command_that_exits_zero_writes_an_empty_findings_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = make_home(tmp, [{"name": "check", "command": ["python3", "-c", "pass"]}])
            result = subprocess.run([sys.executable, JOB, "--home", home, "--name", "check"],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            written = findings_of(os.path.join(home, "jobs", "check", "findings-1.json"))
            self.assertEqual(written, [])

    def test_the_findings_file_records_the_trunk_tip_as_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = make_home(tmp, [{"name": "check", "command": ["python3", "-c", "pass"]}])
            tip = subprocess.run(["git", "rev-parse", "main"], cwd=os.path.join(tmp, "product"),
                                 capture_output=True, text=True, check=True).stdout.strip()
            subprocess.run([sys.executable, JOB, "--home", home, "--name", "check"],
                           capture_output=True, text=True)
            with open(os.path.join(home, "jobs", "check", "findings-1.json"), encoding="utf-8") as fh:
                commit = json.load(fh).get("commit")
            self.assertEqual(commit, tip)

    def test_a_second_run_writes_findings_2_and_keeps_findings_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = make_home(tmp, [{"name": "check", "command": ["python3", "-c", "pass"]}])
            run = [sys.executable, JOB, "--home", home, "--name", "check"]
            subprocess.run(run, capture_output=True, text=True)
            second = subprocess.run(run, capture_output=True, text=True)
            jobs_dir = os.path.join(home, "jobs", "check")
            self.assertEqual((second.returncode, sorted(os.listdir(jobs_dir))),
                             (0, ["findings-1.json", "findings-2.json"]))

    def test_a_failing_command_exits_1_with_one_check_command_finding(self):
        with tempfile.TemporaryDirectory() as tmp:
            command = ["python3", "-c", "import sys; print('boom'); sys.exit(3)"]
            home = make_home(tmp, [{"name": "check", "command": command}])
            result = subprocess.run([sys.executable, JOB, "--home", home, "--name", "check"],
                                    capture_output=True, text=True)
            written = findings_of(os.path.join(home, "jobs", "check", "findings-1.json"))
            fingerprints = [f.get("fingerprint") for f in written or []]
            self.assertEqual((result.returncode, fingerprints), (1, ["check:command"]))


if __name__ == "__main__":
    unittest.main()
