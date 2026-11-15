import importlib.util
import json
import os
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_sentry():
    spec = importlib.util.spec_from_file_location(
        "sentry_adapter", os.path.join(ROOT, "adapters", "sentry.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def issue(issue_id, count):
    return {"id": issue_id, "title": f"Error {issue_id}", "count": count,
            "permalink": f"https://sentry.io/organizations/acme/issues/{issue_id}/"}


class SentryJobTest(unittest.TestCase):
    def test_issue_over_threshold_becomes_the_only_finding(self):
        first, second = issue("101", "5"), issue("102", "1")
        job = {"name": "errors", "adapter": "sentry",
               "config": {"org": "acme", "project_id": 42, "min_events": 3}}

        def fake(url, token):
            return 200, [first, second], {}, None

        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "findings.json")
            argv = ["job", "--job", json.dumps(job), "--repo", tmp, "--commit", "abc123",
                    "--home", tmp, "--out", out]
            with mock.patch.dict(os.environ, {"SENTRY_TOKEN": "tok"}):
                code = load_sentry().main(argv, fetch=fake)
            with open(out) as handle:
                data = json.load(handle)
        findings = data["findings"] if isinstance(data, dict) else data
        self.assertEqual(code, 0)
        self.assertEqual([(f["fingerprint"], f["url"]) for f in findings],
                         [("sentry:101", first["permalink"])])


if __name__ == "__main__":
    unittest.main()
