import importlib.util
import json
import os
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_sonarqube():
    spec = importlib.util.spec_from_file_location(
        "sonarqube_adapter", os.path.join(ROOT, "adapters", "sonarqube.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def issue(key):
    return {"key": key, "message": f"Message {key}", "rule": "python:S1481",
            "component": "web:src/app.py", "line": 7}


class SonarqubeJobTest(unittest.TestCase):
    def test_one_page_of_two_issues_becomes_two_findings_fingerprinted_by_key(self):
        job = {"name": "sonar", "adapter": "sonarqube",
               "config": {"base_url": "https://sq.example", "project_key": "web"}}
        body = {"issues": [issue("AX1"), issue("AX2")],
                "paging": {"pageIndex": 1, "pageSize": 100, "total": 2}}

        def fake(method, url, token):
            return 200, body, None

        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "findings.json")
            argv = ["job", "--job", json.dumps(job), "--repo", "acme/web", "--commit", "abc123",
                    "--home", tmp, "--out", out]
            with mock.patch.dict(os.environ, {"SONAR_TOKEN": "tok"}):
                load_sonarqube().main(argv, fetch=fake)
            with open(out) as handle:
                findings = json.load(handle)["findings"]
        self.assertEqual([f["fingerprint"] for f in findings], ["sonar:AX1", "sonar:AX2"])

    def test_findings_carry_the_issue_messages_as_titles(self):
        job = {"name": "sonar", "adapter": "sonarqube",
               "config": {"base_url": "https://sq.example", "project_key": "web"}}
        body = {"issues": [issue("AX1"), issue("AX2")],
                "paging": {"pageIndex": 1, "pageSize": 100, "total": 2}}

        def fake(method, url, token):
            return 200, body, None

        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "findings.json")
            argv = ["job", "--job", json.dumps(job), "--repo", "acme/web", "--commit", "abc123",
                    "--home", tmp, "--out", out]
            with mock.patch.dict(os.environ, {"SONAR_TOKEN": "tok"}):
                load_sonarqube().main(argv, fetch=fake)
            with open(out) as handle:
                findings = json.load(handle)["findings"]
        self.assertEqual([f["title"] for f in findings], ["Message AX1", "Message AX2"])


if __name__ == "__main__":
    unittest.main()
