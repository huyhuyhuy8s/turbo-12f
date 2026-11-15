import contextlib
import importlib.util
import io
import json
import os
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CREATE_URL = "https://api.github.com/repos/o/r/issues"


def load_adapter():
    spec = importlib.util.spec_from_file_location(
        "job_findings_adapter", os.path.join(ROOT, "adapters", "job-findings.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def finding(fingerprint, title):
    return {"fingerprint": fingerprint, "title": title,
            "detail": f"detail of {title}", "url": f"https://example.test/{fingerprint}"}


class JobFindingsTest(unittest.TestCase):
    def test_new_findings_are_filed_as_one_issue_listing_both_titles(self):
        adapter = load_adapter()
        calls = []

        def fake(method, url, token, payload=None):
            calls.append((method, url, payload))
            return 201, {"number": 9}, None

        with tempfile.TemporaryDirectory() as home:
            os.makedirs(os.path.join(home, "jobs", "nightly"))
            with open(os.path.join(home, "jobs", "nightly", "fingerprints.json"), "w") as fh:
                json.dump({}, fh)
            findings_path = os.path.join(home, "findings.json")
            with open(findings_path, "w") as fh:
                json.dump({"job": "nightly", "kind": "command", "commit": "0123456789abcdef",
                           "at": 0, "findings": [finding("a", "Alpha broke"),
                                                 finding("b", "Beta broke")]}, fh)
            with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "tok"}), \
                    contextlib.redirect_stderr(io.StringIO()):
                adapter.main(["file", "--home", home, "--findings", findings_path,
                              "--repo", "o/r"], fetch=fake)
        posts = [(m, u, json.dumps(p)) for m, u, p in calls if m == "POST"]
        self.assertEqual(
            [(m, u, "Alpha broke" in p and "Beta broke" in p) for m, u, p in posts],
            [("POST", CREATE_URL, True)])

    def test_filed_fingerprints_are_stored_with_the_created_issue_number(self):
        adapter = load_adapter()

        def fake(method, url, token, payload=None):
            return 201, {"number": 9}, None

        with tempfile.TemporaryDirectory() as home:
            os.makedirs(os.path.join(home, "jobs", "nightly"))
            store_path = os.path.join(home, "jobs", "nightly", "fingerprints.json")
            with open(store_path, "w") as fh:
                json.dump({}, fh)
            findings_path = os.path.join(home, "findings.json")
            with open(findings_path, "w") as fh:
                json.dump({"job": "nightly", "kind": "command", "commit": "0123456789abcdef",
                           "at": 0, "findings": [finding("a", "Alpha broke"),
                                                 finding("b", "Beta broke")]}, fh)
            with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "tok"}), \
                    contextlib.redirect_stderr(io.StringIO()):
                adapter.main(["file", "--home", home, "--findings", findings_path,
                              "--repo", "o/r"], fetch=fake)
            with open(store_path) as fh:
                stored = json.load(fh)
        self.assertEqual(stored, {"a": 9, "b": 9})

    def test_finding_still_open_in_store_is_left_out_of_the_new_issue(self):
        adapter = load_adapter()
        calls = []

        def fake(method, url, token, payload=None):
            calls.append((method, url, payload))
            if method == "GET" and url == CREATE_URL + "/7":
                return 200, {"number": 7, "state": "open"}, None
            return 201, {"number": 9}, None

        with tempfile.TemporaryDirectory() as home:
            os.makedirs(os.path.join(home, "jobs", "nightly"))
            with open(os.path.join(home, "jobs", "nightly", "fingerprints.json"), "w") as fh:
                json.dump({"a": 7}, fh)
            findings_path = os.path.join(home, "findings.json")
            with open(findings_path, "w") as fh:
                json.dump({"job": "nightly", "kind": "command", "commit": "0123456789abcdef",
                           "at": 0, "findings": [finding("a", "Alpha broke"),
                                                 finding("c", "Gamma broke")]}, fh)
            with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "tok"}), \
                    contextlib.redirect_stderr(io.StringIO()):
                adapter.main(["file", "--home", home, "--findings", findings_path,
                              "--repo", "o/r"], fetch=fake)
        posts = [(m, u, json.dumps(p)) for m, u, p in calls if m == "POST"]
        self.assertEqual(
            [(m, u, "Gamma broke" in p and "Alpha broke" not in p) for m, u, p in posts],
            [("POST", CREATE_URL, True)])


if __name__ == "__main__":
    unittest.main()
