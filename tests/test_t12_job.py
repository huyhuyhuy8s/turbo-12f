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

    def test_the_failing_command_finding_detail_contains_the_command_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            command = ["python3", "-c", "import sys; print('boom'); sys.exit(3)"]
            home = make_home(tmp, [{"name": "check", "command": command}])
            subprocess.run([sys.executable, JOB, "--home", home, "--name", "check"],
                           capture_output=True, text=True)
            written = findings_of(os.path.join(home, "jobs", "check", "findings-1.json"))
            details = [f.get("detail", "") for f in written or []]
            self.assertIn("boom", "".join(details))

    def test_a_command_exceeding_timeout_s_exits_1_with_one_timed_out_finding(self):
        with tempfile.TemporaryDirectory() as tmp:
            job = {"name": "check", "timeout_s": 1,
                   "command": ["python3", "-c", "import time; time.sleep(5)"]}
            home = make_home(tmp, [job])
            result = subprocess.run([sys.executable, JOB, "--home", home, "--name", "check"],
                                    capture_output=True, text=True)
            written = findings_of(os.path.join(home, "jobs", "check", "findings-1.json")) or []
            details = [f.get("detail", "").lower() for f in written]
            self.assertEqual((result.returncode, len(details), "timed out" in "".join(details)),
                             (1, 1, True))

    def test_a_command_that_cannot_be_started_exits_1_with_one_check_command_finding(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = make_home(tmp, [{"name": "check", "command": ["/nonexistent/t12-no-such-command"]}])
            result = subprocess.run([sys.executable, JOB, "--home", home, "--name", "check"],
                                    capture_output=True, text=True)
            written = findings_of(os.path.join(home, "jobs", "check", "findings-1.json"))
            fingerprints = [f.get("fingerprint") for f in written or []]
            self.assertEqual((result.returncode, fingerprints), (1, ["check:command"]))

    def test_a_failing_command_with_non_utf8_output_still_writes_one_check_command_finding(self):
        with tempfile.TemporaryDirectory() as tmp:
            command = ["python3", "-c", "import sys; sys.stdout.buffer.write(b'bad\\xff\\xfe'); sys.exit(3)"]
            home = make_home(tmp, [{"name": "check", "command": command}])
            result = subprocess.run([sys.executable, JOB, "--home", home, "--name", "check"],
                                    capture_output=True, text=True)
            written = findings_of(os.path.join(home, "jobs", "check", "findings-1.json"))
            fingerprints = [f.get("fingerprint") for f in written or []]
            self.assertEqual((result.returncode, fingerprints), (1, ["check:command"]))

    def test_a_command_job_prints_the_findings_file_path_on_the_last_stdout_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            command = ["python3", "-c", "import sys; print('boom'); sys.exit(3)"]
            home = make_home(tmp, [{"name": "check", "command": command}])
            result = subprocess.run([sys.executable, JOB, "--home", home, "--name", "check"],
                                    capture_output=True, text=True)
            lines = result.stdout.strip().splitlines()
            self.assertEqual(lines[-1] if lines else "", os.path.join(home, "jobs", "check", "findings-1.json"))


FAKE_ADAPTER = """import argparse, json
p = argparse.ArgumentParser()
p.add_argument("mode")
for flag in ("--job", "--repo", "--commit", "--home", "--out"):
    p.add_argument(flag)
a = p.parse_args()
with open(a.out, "w") as fh:
    json.dump({"findings": [{"fingerprint": "f1", "title": "t", "detail": "", "url": None}]}, fh)
"""


class AdapterJobTest(unittest.TestCase):
    def test_an_adapter_job_exits_1_and_records_the_adapter_finding_with_kind_adapter(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = make_home(tmp, [{"name": "fake", "adapter": "fake"}])
            os.makedirs(os.path.join(home, "adapters"))
            with open(os.path.join(home, "adapters", "fake.py"), "w") as fh:
                fh.write(FAKE_ADAPTER)
            result = subprocess.run([sys.executable, JOB, "--home", home, "--name", "fake"],
                                    capture_output=True, text=True)
            path = os.path.join(home, "jobs", "fake", "findings-1.json")
            written = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else {}
            self.assertEqual((result.returncode, written.get("kind"), written.get("findings")),
                             (1, "adapter", [{"fingerprint": "f1", "title": "t", "detail": "", "url": None}]))

    def test_an_adapter_that_exits_1_makes_the_job_exit_2_and_write_no_findings_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = make_home(tmp, [{"name": "fake", "adapter": "fake"}])
            os.makedirs(os.path.join(home, "adapters"))
            with open(os.path.join(home, "adapters", "fake.py"), "w") as fh:
                fh.write(FAKE_ADAPTER + "import sys\nsys.exit(1)\n")
            result = subprocess.run([sys.executable, JOB, "--home", home, "--name", "fake"],
                                    capture_output=True, text=True)
            path = os.path.join(home, "jobs", "fake", "findings-1.json")
            self.assertEqual((result.returncode, os.path.exists(path)), (2, False))

    def test_an_adapter_finding_without_a_fingerprint_makes_the_job_exit_2_and_write_no_findings_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = make_home(tmp, [{"name": "fake", "adapter": "fake"}])
            os.makedirs(os.path.join(home, "adapters"))
            with open(os.path.join(home, "adapters", "fake.py"), "w") as fh:
                fh.write(FAKE_ADAPTER.replace('"fingerprint": "f1", ', ""))
            result = subprocess.run([sys.executable, JOB, "--home", home, "--name", "fake"],
                                    capture_output=True, text=True)
            path = os.path.join(home, "jobs", "fake", "findings-1.json")
            self.assertEqual((result.returncode, os.path.exists(path)), (2, False))

    def test_an_adapter_finding_with_a_non_string_fingerprint_makes_the_job_exit_2_and_write_no_findings_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = make_home(tmp, [{"name": "fake", "adapter": "fake"}])
            os.makedirs(os.path.join(home, "adapters"))
            with open(os.path.join(home, "adapters", "fake.py"), "w") as fh:
                fh.write(FAKE_ADAPTER.replace('"fingerprint": "f1"', '"fingerprint": None'))
            result = subprocess.run([sys.executable, JOB, "--home", home, "--name", "fake"],
                                    capture_output=True, text=True)
            path = os.path.join(home, "jobs", "fake", "findings-1.json")
            self.assertEqual((result.returncode, os.path.exists(path)), (2, False))

    def test_an_adapter_whose_findings_is_not_a_list_makes_the_job_exit_2_and_write_no_findings_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = make_home(tmp, [{"name": "fake", "adapter": "fake"}])
            os.makedirs(os.path.join(home, "adapters"))
            with open(os.path.join(home, "adapters", "fake.py"), "w") as fh:
                fh.write(FAKE_ADAPTER.replace('[{"fingerprint": "f1", "title": "t", "detail": "", "url": None}]', "{}"))
            result = subprocess.run([sys.executable, JOB, "--home", home, "--name", "fake"],
                                    capture_output=True, text=True)
            path = os.path.join(home, "jobs", "fake", "findings-1.json")
            self.assertEqual((result.returncode, os.path.exists(path)), (2, False))


class UnknownJobTest(unittest.TestCase):
    def test_an_unknown_job_name_exits_2_and_lists_the_profile_job_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = make_home(tmp, [{"name": "check", "command": ["true"]}, {"name": "fake", "adapter": "fake"}])
            result = subprocess.run([sys.executable, JOB, "--home", home, "--name", "nope"],
                                    capture_output=True, text=True)
            output = result.stdout + result.stderr
            self.assertEqual((result.returncode, "check" in output, "fake" in output), (2, True, True))

if __name__ == "__main__":
    unittest.main()
