#!/usr/bin/env python3
import json
import os
import sys
import urllib.error
import urllib.request


def _next_url(headers):
    link = next((v for k, v in (headers or {}).items() if k.lower() == "link"), "")
    for part in link.split(","):
        if 'rel="next"' in part and 'results="true"' in part:
            return part[part.index("<") + 1:part.index(">")]
    return None

def _default_fetch(url, token):
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read()), dict(resp.headers), None
    except urllib.error.HTTPError as exc:
        return exc.code, None, dict(exc.headers), str(exc)



def main(argv=None, fetch=None):
    fetch = fetch or _default_fetch
    args = argv[1:]
    job = json.loads(args[args.index("--job") + 1])
    out = args[args.index("--out") + 1]
    config = job["config"]
    if not config.get("org"):
        print("sentry: config.org is required", file=sys.stderr)
        return 1
    url = (f"https://sentry.io/api/0/organizations/{config['org']}/issues/"
           f"?project={config['project_id']}&query=is:unresolved"
           f"&statsPeriod={config.get('period', '14d')}&limit=100")
    token = os.environ.get("SENTRY_TOKEN")
    if not token:
        print("sentry: SENTRY_TOKEN is required", file=sys.stderr)
        return 1
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


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
