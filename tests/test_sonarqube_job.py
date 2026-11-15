import contextlib
import io
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

    def test_total_of_150_requests_page_one_and_page_two_and_no_page_three(self):
        job = {"name": "sonar", "adapter": "sonarqube",
               "config": {"base_url": "https://sq.example", "project_key": "web"}}
        pages = []

        def fake(method, url, token):
            pages.append(int(url.rsplit("&p=", 1)[1]))
            body = {"issues": [issue(f"AX{len(pages)}")],
                    "paging": {"pageIndex": len(pages), "pageSize": 100, "total": 150}}
            return 200, body, None

        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "findings.json")
            argv = ["job", "--job", json.dumps(job), "--repo", "acme/web", "--commit", "abc123",
                    "--home", tmp, "--out", out]
            with mock.patch.dict(os.environ, {"SONAR_TOKEN": "tok"}):
                load_sonarqube().main(argv, fetch=fake)
        self.assertEqual(pages, [1, 2])

    def test_configured_severities_shape_the_first_requested_url(self):
        job = {"name": "sonar", "adapter": "sonarqube",
               "config": {"base_url": "https://sq.example", "project_key": "web",
                          "severities": ["BLOCKER"]}}
        urls = []

        def fake(method, url, token):
            urls.append(url)
            return 200, {"issues": [], "paging": {"pageIndex": 1, "pageSize": 100, "total": 0}}, None

        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "findings.json")
            argv = ["job", "--job", json.dumps(job), "--repo", "acme/web", "--commit", "abc123",
                    "--home", tmp, "--out", out]
            with mock.patch.dict(os.environ, {"SONAR_TOKEN": "tok"}):
                load_sonarqube().main(argv, fetch=fake)
        self.assertEqual(urls[0], "https://sq.example/api/issues/search?componentKeys=web"
                         "&issueStatuses=OPEN,CONFIRMED&impactSeverities=BLOCKER&ps=100&p=1")


    def test_forbidden_status_exits_one_and_names_the_status_on_stderr(self):
        job = {"name": "sonar", "adapter": "sonarqube",
               "config": {"base_url": "https://sq.example", "project_key": "web"}}

        def fake(method, url, token):
            return 403, {"errors": [{"msg": "forbidden"}]}, None

        stderr = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "findings.json")
            argv = ["job", "--job", json.dumps(job), "--repo", "acme/web", "--commit", "abc123",
                    "--home", tmp, "--out", out]
            with mock.patch.dict(os.environ, {"SONAR_TOKEN": "tok"}), \
                    contextlib.redirect_stderr(stderr):
                code = load_sonarqube().main(argv, fetch=fake)
        self.assertEqual(code, 1)
        self.assertIn("403", stderr.getvalue())

    def test_forbidden_status_writes_no_out_file(self):
        job = {"name": "sonar", "adapter": "sonarqube",
               "config": {"base_url": "https://sq.example", "project_key": "web"}}

        def fake(method, url, token):
            return 403, {"errors": [{"msg": "forbidden"}]}, None

        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "findings.json")
            argv = ["job", "--job", json.dumps(job), "--repo", "acme/web", "--commit", "abc123",
                    "--home", tmp, "--out", out]
            with mock.patch.dict(os.environ, {"SONAR_TOKEN": "tok"}), \
                    contextlib.redirect_stderr(io.StringIO()):
                load_sonarqube().main(argv, fetch=fake)
            self.assertFalse(os.path.exists(out))

    def test_config_without_project_key_exits_one_and_never_calls_fetch(self):
        job = {"name": "sonar", "adapter": "sonarqube",
               "config": {"base_url": "https://sq.example"}}
        calls = []

        def fake(method, url, token):
            calls.append(url)
            return 200, {"issues": [], "paging": {"total": 0}}, None

        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "findings.json")
            argv = ["job", "--job", json.dumps(job), "--repo", "acme/web", "--commit", "abc123",
                    "--home", tmp, "--out", out]
            with mock.patch.dict(os.environ, {"SONAR_TOKEN": "tok"}), \
                    contextlib.redirect_stderr(io.StringIO()):
                try:
                    code = load_sonarqube().main(argv, fetch=fake)
                except Exception as error:
                    code = f"raised {type(error).__name__}"
        self.assertEqual(calls, [])
        self.assertEqual(code, 1)

    def test_missing_sonar_token_exits_one_and_never_calls_fetch(self):
        job = {"name": "sonar", "adapter": "sonarqube",
               "config": {"base_url": "https://sq.example", "project_key": "web"}}
        calls = []

        def fake(method, url, token):
            calls.append(url)
            return 200, {"issues": [], "paging": {"total": 0}}, None

        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "findings.json")
            argv = ["job", "--job", json.dumps(job), "--repo", "acme/web", "--commit", "abc123",
                    "--home", tmp, "--out", out]
            with mock.patch.dict(os.environ, {}, clear=True), \
                    contextlib.redirect_stderr(io.StringIO()):
                try:
                    code = load_sonarqube().main(argv, fetch=fake)
                except Exception as error:
                    code = f"raised {type(error).__name__}"
        self.assertEqual((code, calls), (1, []))

    def test_ok_body_without_an_issues_list_exits_one_instead_of_raising(self):
        job = {"name": "sonar", "adapter": "sonarqube",
               "config": {"base_url": "https://sq.example", "project_key": "web"}}

        def fake(method, url, token):
            return 200, {"paging": {"pageIndex": 1, "pageSize": 100, "total": 0}}, None

        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "findings.json")
            argv = ["job", "--job", json.dumps(job), "--repo", "acme/web", "--commit", "abc123",
                    "--home", tmp, "--out", out]
            try:
                with mock.patch.dict(os.environ, {"SONAR_TOKEN": "tok"}), \
                        contextlib.redirect_stderr(io.StringIO()):
                    code = load_sonarqube().main(argv, fetch=fake)
            except Exception as error:
                code = f"raised {type(error).__name__}"
        self.assertEqual(code, 1)

    def test_config_without_base_url_exits_one_and_never_calls_fetch(self):
        job = {"name": "sonar", "adapter": "sonarqube",
               "config": {"project_key": "web"}}
        calls = []

        def fake(method, url, token):
            calls.append(url)
            return 200, {"issues": [], "paging": {"total": 0}}, None

        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "findings.json")
            argv = ["job", "--job", json.dumps(job), "--repo", "acme/web", "--commit", "abc123",
                    "--home", tmp, "--out", out]
            with mock.patch.dict(os.environ, {"SONAR_TOKEN": "tok"}), \
                    contextlib.redirect_stderr(io.StringIO()):
                try:
                    code = load_sonarqube().main(argv, fetch=fake)
                except Exception as error:
                    code = f"raised {type(error).__name__}"
        self.assertEqual((code, calls), (1, []))

if __name__ == "__main__":
    unittest.main()
