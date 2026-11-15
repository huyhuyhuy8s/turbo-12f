#!/usr/bin/env python3
import json
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402


def main(argv=None, fetch=None):
    fetch = fetch or common.request
    with open(argv[argv.index("--findings") + 1]) as handle:
        record = json.load(handle)
    findings = record["findings"]
    repo = argv[argv.index("--repo") + 1]
    payload = {
        "title": f"{len(findings)} findings",
        "body": "\n".join(f"- {finding['title']}" for finding in findings),
    }
    response = fetch("POST", f"https://api.github.com/repos/{repo}/issues",
                     os.environ.get("GITHUB_TOKEN"), payload)
    issue = response[1] if isinstance(response, tuple) else response
    number = (issue or {}).get("number")
    name = argv[argv.index("--name") + 1] if "--name" in argv else record.get("job")
    if "--home" in argv and name:
        job_dir = os.path.join(argv[argv.index("--home") + 1], "jobs", name)
    else:
        job_dir = os.path.dirname(os.path.abspath(argv[argv.index("--findings") + 1]))
    with open(os.path.join(job_dir, "fingerprints.json"), "w") as handle:
        json.dump({finding["fingerprint"]: number for finding in findings}, handle)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
