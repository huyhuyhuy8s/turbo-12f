#!/usr/bin/env python3
import json
import os


def main(argv=None, fetch=None):
    args = argv[1:]
    job = json.loads(args[args.index("--job") + 1])
    out = args[args.index("--out") + 1]
    config = job["config"]
    url = (f"https://sentry.io/api/0/organizations/{config['org']}/issues/"
           f"?project={config['project_id']}&query=is:unresolved"
           f"&statsPeriod={config.get('period', '14d')}&limit=100")
    _status, body, _headers, _error = fetch(url, os.environ["SENTRY_TOKEN"])
    findings = [
        {
            "fingerprint": f"sentry:{issue['id']}",
            "title": issue.get("title", ""),
            "detail": f"{issue['count']} events",
            "url": issue["permalink"],
        }
        for issue in body
        if int(issue["count"]) >= config["min_events"]
    ]
    with open(out, "w") as handle:
        json.dump({"findings": findings}, handle)
    return 0
