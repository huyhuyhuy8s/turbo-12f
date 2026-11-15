#!/usr/bin/env python3
import json
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402


def main(argv=None, fetch=None):
    fetch = fetch or common.request
    with open(argv[argv.index("--findings") + 1]) as handle:
        findings = json.load(handle)["findings"]
    repo = argv[argv.index("--repo") + 1]
    payload = {
        "title": f"{len(findings)} findings",
        "body": "\n".join(f"- {finding['title']}" for finding in findings),
    }
    fetch("POST", f"https://api.github.com/repos/{repo}/issues",
          os.environ.get("GITHUB_TOKEN"), payload)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
