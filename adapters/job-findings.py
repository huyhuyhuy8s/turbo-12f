#!/usr/bin/env python3
import json
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402


def _response_body(response):
    return response[1] if isinstance(response, tuple) else response


def is_open(fetch, repo, number, token):
    if number is None:
        return False
    response = fetch("GET", f"https://api.github.com/repos/{repo}/issues/{number}", token)
    return (_response_body(response) or {}).get("state") != "closed"


def main(argv=None, fetch=None):
    fetch = fetch or common.request
    findings_path = argv[argv.index("--findings") + 1]
    with open(findings_path) as handle:
        record = json.load(handle)
    findings = record["findings"]
    repo = argv[argv.index("--repo") + 1]
    name = argv[argv.index("--name") + 1] if "--name" in argv else record.get("job")
    if "--home" in argv and name:
        job_dir = os.path.join(argv[argv.index("--home") + 1], "jobs", name)
    else:
        job_dir = os.path.dirname(os.path.abspath(findings_path))
    store_path = os.path.join(job_dir, "fingerprints.json")
    store = {}
    if os.path.exists(store_path):
        with open(store_path) as handle:
            store = json.load(handle)
    token = os.environ.get("GITHUB_TOKEN")
    findings = [finding for finding in findings
                if not is_open(fetch, repo, store.get(finding["fingerprint"]), token)]
    payload = {
        "title": f"{len(findings)} findings",
        "body": "\n".join(f"- {finding['title']}\n{finding.get('detail', '')}" for finding in findings),
    }
    response = fetch("POST", f"https://api.github.com/repos/{repo}/issues",
                     token, payload)
    number = (_response_body(response) or {}).get("number")
    store.update({finding["fingerprint"]: number for finding in findings})
    with open(store_path, "w") as handle:
        json.dump(store, handle)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
