import json
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402


def main(argv=None, fetch=None):
    job = json.loads(argv[argv.index("--job") + 1])
    config = job.get("config") or {}
    base_url = config.get("base_url")
    if not base_url:
        print("sonarqube: config has no base_url", file=sys.stderr)
        return 1
    project_key = config.get("project_key")
    if not project_key:
        print("sonarqube: config has no project_key", file=sys.stderr)
        return 1
    severities = ",".join(config.get("severities", ["HIGH", "BLOCKER"]))
    token = os.environ.get("SONAR_TOKEN")
    if not token:
        print("sonarqube: SONAR_TOKEN is not set", file=sys.stderr)
        return 1
    fetch = fetch or common.request
    issues = []
    page = 1
    while True:
        url = (f"{base_url}/api/issues/search?componentKeys={project_key}"
               f"&issueStatuses=OPEN,CONFIRMED&impactSeverities={severities}&ps=100&p={page}")
        status, body, _error = fetch("GET", url, token)
        if status != 200:
            print(f"sonarqube: request returned status {status}", file=sys.stderr)
            return 1
        if not isinstance(body, dict) or not isinstance(body.get("issues"), list):
            print("sonarqube: response has no issues list", file=sys.stderr)
            return 1
        issues.extend(body["issues"])
        if page * 100 >= body.get("paging", {}).get("total", 0) or page >= 10:
            break
        page += 1
    findings = [
        {
            "fingerprint": f"sonar:{issue['key']}",
            "title": issue.get("message", ""),
            "detail": f"{issue.get('rule', '')}\n{issue.get('component', '')}\n{issue.get('line', '')}",
            "url": f"{base_url}/project/issues?id={project_key}&open={issue['key']}",
        }
        for issue in issues
    ]
    with open(argv[argv.index("--out") + 1], "w") as handle:
        json.dump({"findings": findings}, handle)
    return 0
