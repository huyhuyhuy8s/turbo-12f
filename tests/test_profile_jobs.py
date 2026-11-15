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


if __name__ == "__main__":
    unittest.main()
