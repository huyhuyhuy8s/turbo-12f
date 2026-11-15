#!/usr/bin/env python3
import json
import os
import sys


def _next_url(headers):
    link = next((v for k, v in (headers or {}).items() if k.lower() == "link"), "")
    for part in link.split(","):
        if 'rel="next"' in part and 'results="true"' in part:
            return part[part.index("<") + 1:part.index(">")]
    return None


def main(argv=None, fetch=None):
    args = argv[1:]
    job = json.loads(args[args.index("--job") + 1])
    out = args[args.index("--out") + 1]
    config = job["config"]
    url = (f"https://sentry.io/api/0/organizations/{config['org']}/issues/"
           f"?project={config['project_id']}&query=is:unresolved"
           f"&statsPeriod={config.get('period', '14d')}&limit=100")
    token = os.environ["SENTRY_TOKEN"]
    issues = []
    while url:
        status, body, headers, _error = fetch(url, token)
        if status != 200:
            print(f"sentry: HTTP status {status}", file=sys.stderr)
            return 1
        issues.extend(body)
        url = _next_url(headers)
    findings = [
        {
            "fingerprint": f"sentry:{issue['id']}",
            "title": issue.get("title", ""),
            "detail": f"{issue['count']} events",
            "url": issue["permalink"],
        }
        for issue in issues
        if int(issue["count"]) >= config["min_events"]
    ]
    with open(out, "w") as handle:
        json.dump({"findings": findings}, handle)
    return 0
