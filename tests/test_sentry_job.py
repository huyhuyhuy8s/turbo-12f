import contextlib
import io
import importlib.util
import json
import os
import tempfile
import urllib.error
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

    def test_issues_of_every_page_linked_by_next_results_true_are_returned(self):
        job = {"name": "errors", "adapter": "sentry",
               "config": {"org": "acme", "project_id": 42, "min_events": 3}}
        base = "https://sentry.io/api/0/organizations/acme/issues/"
        link = ('<{b}?cursor=0:0:1>; rel="previous"; results="false"; cursor="0:0:1", '
                '<{b}?cursor=0:100:0>; rel="next"; results="{n}"; cursor="0:100:0"')
        pages = [
            (200, [issue("101", "5")], {"Link": link.format(b=base, n="true")}, None),
            (200, [issue("201", "9")], {"Link": link.format(b=base, n="false")}, None),
        ]
        calls = []

        def fake(url, token):
            calls.append(url)
            return pages[min(len(calls), len(pages)) - 1]

        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "findings.json")
            argv = ["job", "--job", json.dumps(job), "--repo", tmp, "--commit", "abc123",
                    "--home", tmp, "--out", out]
            with mock.patch.dict(os.environ, {"SENTRY_TOKEN": "tok"}):
                load_sentry().main(argv, fetch=fake)
            with open(out) as handle:
                findings = json.load(handle)["findings"]
        self.assertEqual([f["fingerprint"] for f in findings], ["sentry:101", "sentry:201"])

    def test_first_url_requested_is_built_from_org_project_and_period(self):
        job = {"name": "errors", "adapter": "sentry",
               "config": {"org": "acme", "project_id": 42, "period": "7d"}}
        calls = []

        def fake(url, token):
            calls.append(url)
            return 200, [], {}, None

        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "findings.json")
            argv = ["job", "--job", json.dumps(job), "--repo", tmp, "--commit", "abc123",
                    "--home", tmp, "--out", out]
            with mock.patch.dict(os.environ, {"SENTRY_TOKEN": "tok"}):
                load_sentry().main(argv, fetch=fake)
        self.assertEqual(calls[:1], [
            "https://sentry.io/api/0/organizations/acme/issues/"
            "?project=42&query=is:unresolved&statsPeriod=7d&limit=100"])

    def test_status_401_exits_1_names_status_and_writes_no_out_file(self):
        job = {"name": "errors", "adapter": "sentry",
               "config": {"org": "acme", "project_id": 42, "min_events": 3}}

        def fake(url, token):
            return 401, [], {}, None

        stderr = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "findings.json")
            argv = ["job", "--job", json.dumps(job), "--repo", tmp, "--commit", "abc123",
                    "--home", tmp, "--out", out]
            with mock.patch.dict(os.environ, {"SENTRY_TOKEN": "tok"}), \
                    contextlib.redirect_stderr(stderr):
                code = load_sentry().main(argv, fetch=fake)
            wrote = os.path.exists(out)
        self.assertEqual((code, "401" in stderr.getvalue(), wrote), (1, True, False))

    def test_config_without_org_exits_1_before_any_request(self):
        job = {"name": "errors", "adapter": "sentry",
               "config": {"project_id": 42, "min_events": 3}}
        calls = []

        def fake(url, token):
            calls.append(url)
            return 200, [], {}, None

        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "findings.json")
            argv = ["job", "--job", json.dumps(job), "--repo", tmp, "--commit", "abc123",
                    "--home", tmp, "--out", out]
            code = None
            with mock.patch.dict(os.environ, {"SENTRY_TOKEN": "tok"}), \
                    contextlib.redirect_stderr(io.StringIO()):
                try:
                    code = load_sentry().main(argv, fetch=fake)
                except KeyError:
                    pass
        self.assertEqual((code, calls), (1, []))

    def test_missing_sentry_token_exits_1_before_any_request(self):
        job = {"name": "errors", "adapter": "sentry",
               "config": {"org": "acme", "project_id": 42, "min_events": 3}}
        calls = []

        def fake(url, token):
            calls.append(url)
            return 200, [], {}, None

        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "findings.json")
            argv = ["job", "--job", json.dumps(job), "--repo", tmp, "--commit", "abc123",
                    "--home", tmp, "--out", out]
            code = None
            with mock.patch.dict(os.environ, {}, clear=True), \
                    contextlib.redirect_stderr(io.StringIO()):
                try:
                    code = load_sentry().main(argv, fetch=fake)
                except KeyError:
                    pass
        self.assertEqual((code, calls), (1, []))

    def test_default_fetch_sends_bearer_get_to_the_issues_url_and_writes_findings(self):
        job = {"name": "errors", "adapter": "sentry",
               "config": {"org": "acme", "project_id": 42, "min_events": 3}}
        first = issue("101", "5")
        requests = []

        class Response:
            status = 200
            headers = {}

            def getcode(self):
                return 200

            def read(self):
                return json.dumps([first]).encode()

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def fake_urlopen(req, *args, **kwargs):
            requests.append((req.full_url, req.get_method(), req.get_header("Authorization")))
            return Response()

        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "findings.json")
            argv = ["job", "--job", json.dumps(job), "--repo", tmp, "--commit", "abc123",
                    "--home", tmp, "--out", out]
            with mock.patch.dict(os.environ, {"SENTRY_TOKEN": "tok-123"}), \
                    mock.patch("urllib.request.urlopen", fake_urlopen):
                load_sentry().main(argv)
            written = None
            if os.path.exists(out):
                with open(out) as handle:
                    written = [f["fingerprint"] for f in json.load(handle)["findings"]]
        self.assertEqual((requests, written), ([(
            "https://sentry.io/api/0/organizations/acme/issues/"
            "?project=42&query=is:unresolved&statsPeriod=14d&limit=100",
            "GET", "Bearer tok-123")], ["sentry:101"]))

    def test_default_fetch_returns_link_headers_so_the_next_page_is_requested(self):
        job = {"name": "errors", "adapter": "sentry",
               "config": {"org": "acme", "project_id": 42, "min_events": 3}}
        first, second = issue("101", "5"), issue("201", "7")
        next_url = "https://sentry.io/api/0/organizations/acme/issues/?cursor=0:100:0"
        link_more = (f'<{next_url}>; rel="next"; results="true"; cursor="0:100:0"')
        link_done = (f'<{next_url}>; rel="next"; results="false"; cursor="0:200:0"')
        pages = [([first], {"Link": link_more}), ([second], {"Link": link_done})]
        requests = []

        class Response:
            status = 200

            def __init__(self, body, headers):
                self.body, self.headers = body, headers

            def getcode(self):
                return 200

            def read(self):
                return json.dumps(self.body).encode()

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def fake_urlopen(req, *args, **kwargs):
            requests.append(req.full_url)
            body, headers = pages[min(len(requests), len(pages)) - 1]
            return Response(body, headers)

        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "findings.json")
            argv = ["job", "--job", json.dumps(job), "--repo", tmp, "--commit", "abc123",
                    "--home", tmp, "--out", out]
            with mock.patch.dict(os.environ, {"SENTRY_TOKEN": "tok-123"}), \
                    mock.patch("urllib.request.urlopen", fake_urlopen):
                load_sentry().main(argv)
            written = None
            if os.path.exists(out):
                with open(out) as handle:
                    written = [f["fingerprint"] for f in json.load(handle)["findings"]]
        self.assertEqual((len(requests), requests[1:], written),
                         (2, [next_url], ["sentry:101", "sentry:201"]))

    def test_next_page_is_requested_at_the_rel_next_link_with_its_cursor(self):
        job = {"name": "errors", "adapter": "sentry",
               "config": {"org": "acme", "project_id": 42, "period": "7d", "min_events": 3}}
        base = "https://sentry.io/api/0/organizations/acme/issues/"
        link = ('<{b}?cursor=0:0:1>; rel="previous"; results="false"; cursor="0:0:1", '
                '<{b}?cursor=0:100:0>; rel="next"; results="{n}"; cursor="0:100:0"')
        pages = [
            (200, [issue("101", "5")], {"Link": link.format(b=base, n="true")}, None),
            (200, [issue("201", "9")], {"Link": link.format(b=base, n="false")}, None),
        ]
        calls = []

        def fake(url, token):
            calls.append(url)
            return pages[min(len(calls), len(pages)) - 1]

        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "findings.json")
            argv = ["job", "--job", json.dumps(job), "--repo", tmp, "--commit", "abc123",
                    "--home", tmp, "--out", out]
            with mock.patch.dict(os.environ, {"SENTRY_TOKEN": "tok"}):
                load_sentry().main(argv, fetch=fake)
            with open(out) as handle:
                ids = [f["fingerprint"] for f in json.load(handle)["findings"]]
        self.assertEqual((calls, ids), ([
            base + "?project=42&query=is:unresolved&statsPeriod=7d&limit=100",
            base + "?cursor=0:100:0"], ["sentry:101", "sentry:201"]))

    def test_default_fetch_401_from_urlopen_exits_1_names_status_and_writes_no_out_file(self):
        job = {"name": "errors", "adapter": "sentry",
               "config": {"org": "acme", "project_id": 42, "min_events": 3}}

        def fake_urlopen(req, *args, **kwargs):
            raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, None)

        stderr = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "findings.json")
            argv = ["job", "--job", json.dumps(job), "--repo", tmp, "--commit", "abc123",
                    "--home", tmp, "--out", out]
            with mock.patch.dict(os.environ, {"SENTRY_TOKEN": "tok"}), \
                    mock.patch("urllib.request.urlopen", fake_urlopen), \
                    contextlib.redirect_stderr(stderr):
                try:
                    code = load_sentry().main(argv)
                except Exception as exc:
                    code = type(exc).__name__
            wrote = os.path.exists(out)
        self.assertEqual((code, "401" in stderr.getvalue(), wrote), (1, True, False))



if __name__ == "__main__":
    unittest.main()
