import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from t12 import profile  # noqa: E402
from t12.errors import StateError  # noqa: E402

JOBS = [
    {"name": "fuzz", "command": ["python3", "fuzz.py"], "timeout_s": 600},
    {"name": "errors", "adapter": "sentry", "config": {"org": "acme"}, "label": "production"},
]


class ProfileJobsTest(unittest.TestCase):
    def test_parse_returns_the_given_jobs(self):
        parsed = profile.parse(json.dumps({"version": 1, "jobs": JOBS}))
        self.assertEqual(parsed["jobs"], JOBS)

    def test_job_with_both_command_and_adapter_is_refused_naming_the_job(self):
        job = {"name": "mixed", "command": ["python3", "x.py"], "adapter": "sentry"}
        message = ""
        try:
            profile.parse(json.dumps({"version": 1, "jobs": [job]}))
        except StateError as exc:
            message = str(exc)
        self.assertIn("mixed", message)

    def test_job_with_neither_command_nor_adapter_is_refused_naming_the_job(self):
        job = {"name": "hollow", "timeout_s": 5}
        message = ""
        try:
            profile.parse(json.dumps({"version": 1, "jobs": [job]}))
        except StateError as exc:
            message = str(exc)
        self.assertIn("hollow", message)

    def test_two_jobs_with_the_same_name_are_refused_naming_the_name(self):
        jobs = [
            {"name": "fuzz", "command": ["python3", "a.py"]},
            {"name": "fuzz", "command": ["python3", "b.py"]},
        ]
        message = ""
        try:
            profile.parse(json.dumps({"version": 1, "jobs": jobs}))
        except StateError as exc:
            message = str(exc)
        self.assertIn("fuzz", message)

    def test_job_named_with_uppercase_and_underscore_is_refused_naming_the_name(self):
        jobs = [{"name": "Fuzz_1", "command": ["python3", "a.py"]}]
        message = ""
        try:
            profile.parse(json.dumps({"version": 1, "jobs": jobs}))
        except StateError as exc:
            message = str(exc)
        self.assertIn("Fuzz_1", message)

    def test_adapter_job_with_timeout_s_is_refused_naming_the_key(self):
        jobs = [{"name": "errors", "adapter": "sentry", "timeout_s": 30}]
        message = ""
        try:
            profile.parse(json.dumps({"version": 1, "jobs": jobs}))
        except StateError as exc:
            message = str(exc)
        self.assertIn("timeout_s", message)

    def test_command_job_with_config_is_refused_naming_the_key(self):
        jobs = [{"name": "fuzz", "command": ["python3", "a.py"], "config": {"org": "acme"}}]
        message = ""
        try:
            profile.parse(json.dumps({"version": 1, "jobs": jobs}))
        except StateError as exc:
            message = str(exc)
        self.assertIn("config", message)

    def test_job_with_a_schedule_key_is_refused_naming_the_key(self):
        jobs = [{"name": "fuzz", "command": ["python3", "a.py"], "schedule": "0 * * * *"}]
        message = ""
        try:
            profile.parse(json.dumps({"version": 1, "jobs": jobs}))
        except StateError as exc:
            message = str(exc)
        self.assertIn("schedule", message)

    def test_command_job_whose_argv_check_command_refuses_is_refused_naming_the_job(self):
        jobs = [{"name": "risky", "command": ["python3", "x.py", "--no-sandbox"]}]
        message = ""
        try:
            profile.parse(json.dumps({"version": 1, "jobs": jobs}))
        except StateError as exc:
            message = str(exc)
        self.assertIn("risky", message)

    def test_apply_refuses_a_task_file_that_sets_jobs(self):
        base = profile.parse(json.dumps({"version": 1}))
        task = {"test_command": ["true"], "jobs": JOBS}
        message = ""
        try:
            profile.apply(base, task)
        except StateError as exc:
            message = str(exc)
        self.assertIn("jobs belongs in", message)

    def test_job_name_with_a_trailing_newline_is_refused_naming_the_name(self):
        jobs = [{"name": "fuzz\n", "command": ["python3", "a.py"]}]
        message = ""
        try:
            profile.parse(json.dumps({"version": 1, "jobs": jobs}))
        except StateError as exc:
            message = str(exc)
        self.assertIn("fuzz", message)

    def test_command_given_as_a_string_with_a_bypass_flag_is_refused_naming_the_job(self):
        jobs = [{"name": "risky", "command": "python3 x.py --no-sandbox"}]
        message = ""
        try:
            profile.parse(json.dumps({"version": 1, "jobs": jobs}))
        except StateError as exc:
            message = str(exc)
        self.assertIn("risky", message)

    def test_job_with_an_unknown_key_other_than_schedule_is_refused_naming_the_key(self):
        jobs = [{"name": "fuzz", "command": ["python3", "a.py"], "retries": 3}]
        message = ""
        try:
            profile.parse(json.dumps({"version": 1, "jobs": jobs}))
        except StateError as exc:
            message = str(exc)
        self.assertIn("retries", message)


    def test_string_command_with_a_quoted_bypass_flag_is_refused_naming_the_job(self):
        jobs = [{"name": "sneaky", "command": "python3 x.py '--no-sandbox'"}]
        message = ""
        try:
            profile.parse(json.dumps({"version": 1, "jobs": jobs}))
        except StateError as exc:
            message = str(exc)
        self.assertIn("sneaky", message)


    def test_jobs_given_as_an_object_instead_of_a_list_is_refused_naming_jobs(self):
        message = ""
        try:
            profile.parse(json.dumps({"version": 1, "jobs": {}}))
        except StateError as exc:
            message = str(exc)
        self.assertIn("jobs", message)

    def test_job_that_is_not_an_object_is_refused_with_a_state_error(self):
        caught = None
        try:
            profile.parse(json.dumps({"version": 1, "jobs": ["fuzz"]}))
        except Exception as exc:  # noqa: BLE001
            caught = exc
        self.assertIsInstance(caught, StateError)

    def test_string_command_with_an_unterminated_quote_is_refused_naming_the_job(self):
        jobs = [{"name": "broken", "command": "python3 'x.py"}]
        caught = None
        try:
            profile.parse(json.dumps({"version": 1, "jobs": jobs}))
        except Exception as exc:  # noqa: BLE001
            caught = exc
        self.assertIsInstance(caught, StateError)

    def test_command_that_is_neither_a_string_nor_a_list_is_refused_with_a_state_error(self):
        jobs = [{"name": "odd", "command": 5}]
        caught = None
        try:
            profile.parse(json.dumps({"version": 1, "jobs": jobs}))
        except Exception as exc:  # noqa: BLE001
            caught = exc
        self.assertIsInstance(caught, StateError)


if __name__ == "__main__":
    unittest.main()
