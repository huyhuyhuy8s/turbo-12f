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

    def test_an_adapter_finding_with_an_empty_fingerprint_makes_the_job_exit_2_and_write_no_findings_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = make_home(tmp, [{"name": "fake", "adapter": "fake"}])
            os.makedirs(os.path.join(home, "adapters"))
            with open(os.path.join(home, "adapters", "fake.py"), "w") as fh:
                fh.write(FAKE_ADAPTER.replace('"fingerprint": "f1"', '"fingerprint": ""'))
            result = subprocess.run([sys.executable, JOB, "--home", home, "--name", "fake"],
                                    capture_output=True, text=True)
            path = os.path.join(home, "jobs", "fake", "findings-1.json")
            self.assertEqual((result.returncode, os.path.exists(path)), (2, False))

    def test_an_adapter_finding_with_a_non_string_title_makes_the_job_exit_2_and_write_no_findings_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = make_home(tmp, [{"name": "fake", "adapter": "fake"}])
            os.makedirs(os.path.join(home, "adapters"))
            with open(os.path.join(home, "adapters", "fake.py"), "w") as fh:
                fh.write(FAKE_ADAPTER.replace('"title": "t"', '"title": 5'))
            result = subprocess.run([sys.executable, JOB, "--home", home, "--name", "fake"],
                                    capture_output=True, text=True)
            path = os.path.join(home, "jobs", "fake", "findings-1.json")
            self.assertEqual((result.returncode, os.path.exists(path)), (2, False))

    def test_an_adapter_finding_with_a_non_string_detail_makes_the_job_exit_2_and_write_no_findings_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = make_home(tmp, [{"name": "fake", "adapter": "fake"}])
            os.makedirs(os.path.join(home, "adapters"))
            with open(os.path.join(home, "adapters", "fake.py"), "w") as fh:
                fh.write(FAKE_ADAPTER.replace('"detail": ""', '"detail": 5'))
            result = subprocess.run([sys.executable, JOB, "--home", home, "--name", "fake"],
                                    capture_output=True, text=True)
            path = os.path.join(home, "jobs", "fake", "findings-1.json")
            self.assertEqual((result.returncode, os.path.exists(path)), (2, False))

    def test_an_adapter_finding_with_a_url_that_is_neither_string_nor_null_makes_the_job_exit_2_and_write_no_findings_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = make_home(tmp, [{"name": "fake", "adapter": "fake"}])
            os.makedirs(os.path.join(home, "adapters"))
            with open(os.path.join(home, "adapters", "fake.py"), "w") as fh:
                fh.write(FAKE_ADAPTER.replace('"url": None', '"url": 5'))
            result = subprocess.run([sys.executable, JOB, "--home", home, "--name", "fake"],
                                    capture_output=True, text=True)
            path = os.path.join(home, "jobs", "fake", "findings-1.json")
            self.assertEqual((result.returncode, os.path.exists(path)), (2, False))

    def test_an_adapter_that_prints_without_a_trailing_newline_still_leaves_the_path_on_the_last_stdout_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = make_home(tmp, [{"name": "fake", "adapter": "fake"}])
            os.makedirs(os.path.join(home, "adapters"))
            with open(os.path.join(home, "adapters", "fake.py"), "w") as fh:
                fh.write(FAKE_ADAPTER + "import sys\nsys.stdout.write('noise')\n")
            result = subprocess.run([sys.executable, JOB, "--home", home, "--name", "fake"],
                                    capture_output=True, text=True)
            lines = result.stdout.strip().splitlines()
            self.assertEqual(lines[-1] if lines else "", os.path.join(home, "jobs", "fake", "findings-1.json"))


class UnknownJobTest(unittest.TestCase):
    def test_an_unknown_job_name_exits_2_and_lists_the_profile_job_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = make_home(tmp, [{"name": "check", "command": ["true"]}, {"name": "fake", "adapter": "fake"}])
            result = subprocess.run([sys.executable, JOB, "--home", home, "--name", "nope"],
                                    capture_output=True, text=True)
            output = result.stdout + result.stderr
            self.assertEqual((result.returncode, "check" in output, "fake" in output), (2, True, True))

    def test_an_unknown_job_name_creates_no_job_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = make_home(tmp, [{"name": "check", "command": ["true"]}])
            result = subprocess.run([sys.executable, JOB, "--home", home, "--name", "nope"],
                                    capture_output=True, text=True)
            self.assertEqual((result.returncode, os.path.exists(os.path.join(home, "jobs", "nope"))), (2, False))


class AdapterNameTest(unittest.TestCase):
    def test_an_adapter_given_as_an_absolute_path_is_not_run_and_exits_2_with_no_findings_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = os.path.join(tmp, "ran")
            with open(os.path.join(tmp, "evil.py"), "w") as fh:
                fh.write(FAKE_ADAPTER + "open(%r, 'w').close()\n" % marker)
            home = make_home(tmp, [{"name": "fake", "adapter": os.path.join(tmp, "evil")}])
            result = subprocess.run([sys.executable, JOB, "--home", home, "--name", "fake"],
                                    capture_output=True, text=True)
            path = os.path.join(home, "jobs", "fake", "findings-1.json")
            self.assertEqual((result.returncode, os.path.exists(marker), os.path.exists(path)), (2, False, False))

    def test_an_adapter_given_as_a_relative_traversal_is_not_run_and_exits_2_with_no_findings_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = os.path.join(tmp, "ran")
            with open(os.path.join(tmp, "product-evil.py"), "w") as fh:
                fh.write(FAKE_ADAPTER + "open(%r, 'w').close()\n" % marker)
            home = make_home(tmp, [{"name": "fake", "adapter": "../../product/evil"}])
            os.makedirs(os.path.join(home, "adapters"))
            os.rename(os.path.join(tmp, "product-evil.py"), os.path.join(tmp, "product", "evil.py"))
            result = subprocess.run([sys.executable, JOB, "--home", home, "--name", "fake"],
                                    capture_output=True, text=True)
            path = os.path.join(home, "jobs", "fake", "findings-1.json")
            self.assertEqual((result.returncode, os.path.exists(marker), os.path.exists(path)), (2, False, False))


class CommandEnvironmentTest(unittest.TestCase):
    def test_a_command_job_does_not_see_the_factorys_forbidden_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = make_home(tmp, [{"name": "check", "command": ["sh", "-c", "echo $GITHUB_TOKEN; exit 1"]}])
            result = subprocess.run([sys.executable, JOB, "--home", home, "--name", "check"],
                                    capture_output=True, text=True,
                                    env={**os.environ, "GITHUB_TOKEN": "secret123"})
            with open(os.path.join(home, "jobs", "check", "findings-1.json"), encoding="utf-8") as fh:
                details = [f["detail"] for f in json.load(fh)["findings"]]
            self.assertEqual((result.returncode, len(details), "secret123" in "".join(details)), (1, 1, False))


class RejectedProfileTest(unittest.TestCase):
    def test_a_profile_job_the_profile_parser_rejects_exits_2_runs_nothing_and_writes_no_findings_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = os.path.join(tmp, "ran")
            command = ["python3", "-c", "open(%r, 'w').close()" % marker, "--dangerously-skip-permissions"]
            home = make_home(tmp, [{"name": "c", "command": command}])
            result = subprocess.run([sys.executable, JOB, "--home", home, "--name", "c"],
                                    capture_output=True, text=True)
            path = os.path.join(home, "jobs", "c", "findings-1.json")
            self.assertEqual(
                (result.returncode, os.path.exists(marker), os.path.exists(path),
                 "permission bypass flag" in result.stdout + result.stderr),
                (2, False, False, True))


class TrunkCheckoutTest(unittest.TestCase):
    def test_a_command_job_runs_on_the_trunk_tip_whatever_the_repo_has_checked_out(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = make_home(tmp, [{"name": "check", "command": ["test", "-f", "ok"]}])
            repo = os.path.join(tmp, "product")
            with open(os.path.join(repo, "ok"), "w") as fh:
                fh.write("ok")
            for args in (["add", "ok"], ["commit", "-qm", "add ok"], ["checkout", "-q", "--detach", "main~1"]):
                git(repo, *args)
            tip = subprocess.run(["git", "rev-parse", "main"], cwd=repo,
                                 capture_output=True, text=True, check=True).stdout.strip()
            result = subprocess.run([sys.executable, JOB, "--home", home, "--name", "check"],
                                    capture_output=True, text=True)
            with open(os.path.join(home, "jobs", "check", "findings-1.json"), encoding="utf-8") as fh:
                record = json.load(fh)
            self.assertEqual((result.returncode, record["findings"], record["commit"]), (0, [], tip))


class CommandRecordTest(unittest.TestCase):
    def test_a_failing_command_job_records_its_name_and_kind_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = make_home(tmp, [{"name": "check", "command": ["python3", "-c", "import sys; sys.exit(3)"]}])
            subprocess.run([sys.executable, JOB, "--home", home, "--name", "check"],
                           capture_output=True, text=True)
            with open(os.path.join(home, "jobs", "check", "findings-1.json"), encoding="utf-8") as fh:
                record = json.load(fh)
            self.assertEqual((record.get("job"), record.get("kind")), ("check", "command"))

    def test_a_failing_command_job_records_at_as_epoch_seconds_near_now(self):
        import time
        with tempfile.TemporaryDirectory() as tmp:
            home = make_home(tmp, [{"name": "check", "command": ["python3", "-c", "import sys; sys.exit(3)"]}])
            before = time.time()
            subprocess.run([sys.executable, JOB, "--home", home, "--name", "check"],
                           capture_output=True, text=True)
            after = time.time()
            with open(os.path.join(home, "jobs", "check", "findings-1.json"), encoding="utf-8") as fh:
                record = json.load(fh)
            at = record.get("at")
            self.assertTrue(isinstance(at, (int, float)) and before - 5 <= at <= after + 5,
                            "at=%r not epoch seconds in [%s, %s]" % (at, before, after))


    def test_a_failing_command_finding_has_a_string_title_and_a_null_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = make_home(tmp, [{"name": "check", "command": ["python3", "-c", "import sys; sys.exit(3)"]}])
            subprocess.run([sys.executable, JOB, "--home", home, "--name", "check"],
                           capture_output=True, text=True)
            with open(os.path.join(home, "jobs", "check", "findings-1.json"), encoding="utf-8") as fh:
                finding = json.load(fh)["findings"][0]
            self.assertEqual((isinstance(finding.get("title"), str), finding.get("url", "missing")), (True, None))


class AdapterValueTypeTest(unittest.TestCase):
    def test_an_adapter_value_that_is_not_a_string_exits_2_with_no_findings_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = make_home(tmp, [{"name": "fake", "adapter": 5}])
            result = subprocess.run([sys.executable, JOB, "--home", home, "--name", "fake"],
                                    capture_output=True, text=True)
            path = os.path.join(home, "jobs", "fake", "findings-1.json")
            self.assertEqual((result.returncode, os.path.exists(path)), (2, False))


class CommandStdinTest(unittest.TestCase):
    def test_a_command_job_reads_end_of_input_from_stdin_not_the_runners_stdin(self):
        with tempfile.TemporaryDirectory() as tmp:
            command = ["python3", "-c", "import sys; sys.exit(1 if sys.stdin.read() else 0)"]
            home = make_home(tmp, [{"name": "check", "command": command}])
            result = subprocess.run([sys.executable, JOB, "--home", home, "--name", "check"],
                                    input="runner stdin", capture_output=True, text=True)
            path = os.path.join(home, "jobs", "check", "findings-1.json")
            self.assertEqual((result.returncode, findings_of(path)), (0, []))


class CommandTimeoutValueTest(unittest.TestCase):
    def test_a_timeout_s_that_is_not_a_number_exits_2_with_no_findings_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = make_home(tmp, [{"name": "check", "timeout_s": "soon", "command": ["python3", "-c", "pass"]}])
            result = subprocess.run([sys.executable, JOB, "--home", home, "--name", "check"],
                                    capture_output=True, text=True)
            path = os.path.join(home, "jobs", "check", "findings-1.json")
            self.assertEqual((result.returncode, os.path.exists(path)), (2, False))


class MissingTrunkTest(unittest.TestCase):
    def test_a_home_whose_trunk_branch_does_not_exist_exits_2_not_1_and_writes_no_findings_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = make_home(tmp, [{"name": "check", "command": ["python3", "-c", "pass"]}])
            with open(os.path.join(home, "config.json")) as fh:
                config = json.load(fh)
            config["trunk"] = "no-such-branch"
            with open(os.path.join(home, "config.json"), "w") as fh:
                json.dump(config, fh)
            result = subprocess.run([sys.executable, JOB, "--home", home, "--name", "check"],
                                    capture_output=True, text=True)
            path = os.path.join(home, "jobs", "check", "findings-1.json")
            self.assertEqual((result.returncode, os.path.exists(path)), (2, False))


class MissingConfigTest(unittest.TestCase):
    def test_a_home_without_a_config_file_exits_2_not_1_and_writes_no_findings_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = make_home(tmp, [{"name": "check", "command": ["python3", "-c", "pass"]}])
            os.remove(os.path.join(home, "config.json"))
            result = subprocess.run([sys.executable, JOB, "--home", home, "--name", "check"],
                                    capture_output=True, text=True)
            path = os.path.join(home, "jobs", "check", "findings-1.json")
            self.assertEqual((result.returncode, os.path.exists(path)), (2, False))


class MalformedConfigTest(unittest.TestCase):
    def test_a_home_whose_config_file_is_not_valid_json_exits_2_not_1_and_writes_no_findings_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = make_home(tmp, [{"name": "check", "command": ["python3", "-c", "pass"]}])
            with open(os.path.join(home, "config.json"), "w") as fh:
                fh.write("{not json")
            result = subprocess.run([sys.executable, JOB, "--home", home, "--name", "check"],
                                    capture_output=True, text=True)
            path = os.path.join(home, "jobs", "check", "findings-1.json")
            self.assertEqual((result.returncode, os.path.exists(path)), (2, False))


class ConfigWithoutRepoTest(unittest.TestCase):
    def test_a_home_whose_config_has_no_repo_key_exits_2_not_1_and_writes_no_findings_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = make_home(tmp, [{"name": "check", "command": ["python3", "-c", "pass"]}])
            with open(os.path.join(home, "config.json"), "w") as fh:
                json.dump({"version": 1, "trunk": "main"}, fh)
            result = subprocess.run([sys.executable, JOB, "--home", home, "--name", "check"],
                                    capture_output=True, text=True)
            path = os.path.join(home, "jobs", "check", "findings-1.json")
            self.assertEqual((result.returncode, os.path.exists(path)), (2, False))


class ConfigNotAnObjectTest(unittest.TestCase):
    def test_a_home_whose_config_file_is_not_a_json_object_exits_2_not_1_and_writes_no_findings_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = make_home(tmp, [{"name": "check", "command": ["python3", "-c", "pass"]}])
            with open(os.path.join(home, "config.json"), "w") as fh:
                fh.write("null")
            result = subprocess.run([sys.executable, JOB, "--home", home, "--name", "check"],
                                    capture_output=True, text=True)
            path = os.path.join(home, "jobs", "check", "findings-1.json")
            self.assertEqual((result.returncode, os.path.exists(path)), (2, False))


if __name__ == "__main__":
    unittest.main()


class CommandTimeoutNotFiniteTest(unittest.TestCase):
    def test_a_timeout_s_of_nan_exits_2_with_no_findings_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = make_home(tmp, [{"name": "check", "timeout_s": float("nan"), "command": ["python3", "-c", "pass"]}])
            result = subprocess.run([sys.executable, JOB, "--home", home, "--name", "check"],
                                    capture_output=True, text=True)
            path = os.path.join(home, "jobs", "check", "findings-1.json")
            self.assertEqual((result.returncode, os.path.exists(path)), (2, False))


class CommandTimeoutInfiniteTest(unittest.TestCase):
    def test_a_timeout_s_of_infinity_exits_2_with_no_findings_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = make_home(tmp, [{"name": "check", "timeout_s": float("inf"), "command": ["python3", "-c", "pass"]}])
            result = subprocess.run([sys.executable, JOB, "--home", home, "--name", "check"],
                                    capture_output=True, text=True)
            path = os.path.join(home, "jobs", "check", "findings-1.json")
            self.assertEqual((result.returncode, os.path.exists(path)), (2, False))


class CommandTimeoutNotPositiveTest(unittest.TestCase):
    def test_a_timeout_s_of_zero_exits_2_with_no_findings_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = make_home(tmp, [{"name": "check", "timeout_s": 0, "command": ["python3", "-c", "pass"]}])
            result = subprocess.run([sys.executable, JOB, "--home", home, "--name", "check"],
                                    capture_output=True, text=True)
            path = os.path.join(home, "jobs", "check", "findings-1.json")
            self.assertEqual((result.returncode, os.path.exists(path)), (2, False))


class ConfigRepoNotAStringTest(unittest.TestCase):
    def test_a_home_whose_config_repo_is_not_a_string_exits_2_not_1_and_writes_no_findings_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = make_home(tmp, [{"name": "check", "command": ["python3", "-c", "pass"]}])
            with open(os.path.join(home, "config.json"), "w") as fh:
                json.dump({"version": 1, "repo": 5, "trunk": "main"}, fh)
            result = subprocess.run([sys.executable, JOB, "--home", home, "--name", "check"],
                                    capture_output=True, text=True)
            path = os.path.join(home, "jobs", "check", "findings-1.json")
            self.assertEqual((result.returncode, os.path.exists(path)), (2, False))


class ConfigTrunkNotAStringTest(unittest.TestCase):
    def test_a_home_whose_config_trunk_is_not_a_string_exits_2_not_1_and_writes_no_findings_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = make_home(tmp, [{"name": "check", "command": ["python3", "-c", "pass"]}])
            with open(os.path.join(home, "config.json"), "w") as fh:
                json.dump({"version": 1, "repo": os.path.join(tmp, "product"), "trunk": 5}, fh)
            result = subprocess.run([sys.executable, JOB, "--home", home, "--name", "check"],
                                    capture_output=True, text=True)
            path = os.path.join(home, "jobs", "check", "findings-1.json")
            self.assertEqual((result.returncode, os.path.exists(path)), (2, False))


class CommandFingerprintNameTest(unittest.TestCase):
    def test_a_failing_command_job_named_lint_reports_a_lint_command_fingerprint(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = make_home(tmp, [{"name": "lint", "command": ["python3", "-c", "import sys; sys.exit(3)"]}])
            result = subprocess.run([sys.executable, JOB, "--home", home, "--name", "lint"],
                                    capture_output=True, text=True)
            written = findings_of(os.path.join(home, "jobs", "lint", "findings-1.json"))
            fingerprints = [f.get("fingerprint") for f in written or []]
            self.assertEqual((result.returncode, fingerprints), (1, ["lint:command"]))


class CommandHomeTest(unittest.TestCase):
    def test_a_command_job_cannot_read_credential_files_from_the_operators_home(self):
        with tempfile.TemporaryDirectory() as tmp:
            real_home = os.path.join(tmp, "operator")
            os.makedirs(os.path.join(real_home, ".config", "gh"))
            with open(os.path.join(real_home, ".config", "gh", "hosts.yml"), "w") as fh:
                fh.write("oauth_token: ghp_SECRET123\n")
            script = ("import os, sys; print(open(os.path.expanduser('~/.config/gh/hosts.yml')).read()); sys.exit(1)")
            home = make_home(tmp, [{"name": "check", "command": ["python3", "-c", script]}])
            result = subprocess.run([sys.executable, JOB, "--home", home, "--name", "check"],
                                    capture_output=True, text=True, env={**os.environ, "HOME": real_home})
            with open(os.path.join(home, "jobs", "check", "findings-1.json"), encoding="utf-8") as fh:
                details = [f["detail"] for f in json.load(fh)["findings"]]
            self.assertEqual((result.returncode, len(details), "ghp_SECRET123" in "".join(details)), (1, 1, False))


class CommandCageTest(unittest.TestCase):
    def test_a_command_job_cannot_read_a_file_outside_its_checkout_where_the_cage_is_available(self):
        sys.path.insert(0, ROOT)
        from t12 import sandbox
        if not sandbox.landlock_available():
            self.skipTest("no landlock cage on this kernel")
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside:
            secret_path = os.path.join(outside, "notes.txt")
            with open(secret_path, "w") as fh:
                fh.write("CAGE-ESCAPE-4711\n")
            script = "import sys; print(open(%r).read()); sys.exit(1)" % secret_path
            home = make_home(tmp, [{"name": "check", "command": ["python3", "-c", script]}])
            result = subprocess.run([sys.executable, JOB, "--home", home, "--name", "check"],
                                    capture_output=True, text=True)
            with open(os.path.join(home, "jobs", "check", "findings-1.json"), encoding="utf-8") as fh:
                details = [f["detail"] for f in json.load(fh)["findings"]]
            self.assertEqual((result.returncode, len(details), "CAGE-ESCAPE-4711" in "".join(details)), (1, 1, False))


class CommandCheckoutAttributesTest(unittest.TestCase):
    def test_a_command_job_sees_a_tracked_file_that_gitattributes_marks_export_ignore(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = make_home(tmp, [{"name": "check", "command": ["test", "-f", "ok"]}])
            repo = os.path.join(tmp, "product")
            with open(os.path.join(repo, "ok"), "w") as fh:
                fh.write("ok")
            with open(os.path.join(repo, ".gitattributes"), "w") as fh:
                fh.write("ok export-ignore\n")
            for args in (["add", "ok", ".gitattributes"], ["commit", "-qm", "add ok"]):
                git(repo, *args)
            result = subprocess.run([sys.executable, JOB, "--home", home, "--name", "check"],
                                    capture_output=True, text=True)
            written = findings_of(os.path.join(home, "jobs", "check", "findings-1.json"))
            self.assertEqual((result.returncode, written), (0, []))


class CommandTimeoutHugeTest(unittest.TestCase):
    def test_a_timeout_s_too_large_for_a_float_exits_2_with_no_findings_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = make_home(tmp, [{"name": "check", "timeout_s": 10 ** 400, "command": ["python3", "-c", "pass"]}])
            result = subprocess.run([sys.executable, JOB, "--home", home, "--name", "check"],
                                    capture_output=True, text=True)
            path = os.path.join(home, "jobs", "check", "findings-1.json")
            self.assertEqual((result.returncode, os.path.exists(path)), (2, False))


class CommandCheckoutFailureTest(unittest.TestCase):
    def test_a_trunk_checkout_that_git_cannot_make_never_exits_1_without_a_findings_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = make_home(tmp, [{"name": "check", "command": ["true"]}])
            repo = os.path.join(tmp, "product")
            with open(os.path.join(repo, ".gitattributes"), "w") as fh:
                fh.write("* filter=broken\n")
            for args in (["config", "filter.broken.clean", "cat"], ["config", "filter.broken.smudge", "false"],
                         ["config", "filter.broken.required", "true"], ["add", ".gitattributes"],
                         ["commit", "-qm", "add broken filter"]):
                git(repo, *args)
            result = subprocess.run([sys.executable, JOB, "--home", home, "--name", "check"],
                                    capture_output=True, text=True)
            path = os.path.join(home, "jobs", "check", "findings-1.json")
            self.assertFalse(result.returncode == 1 and not os.path.exists(path), result.stderr[-300:])


class CommandTimeoutLongTest(unittest.TestCase):
    def test_a_command_with_a_very_long_timeout_s_runs_normally_and_writes_an_empty_findings_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = make_home(tmp, [{"name": "check", "timeout_s": 10 ** 7, "command": ["python3", "-c", "pass"]}])
            result = subprocess.run([sys.executable, JOB, "--home", home, "--name", "check"],
                                    capture_output=True, text=True)
            written = findings_of(os.path.join(home, "jobs", "check", "findings-1.json"))
            self.assertEqual((result.returncode, written), (0, []), result.stderr[-300:])
