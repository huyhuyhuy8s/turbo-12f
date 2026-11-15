import json
import os


def main(argv=None, fetch=None):
    job = json.loads(argv[argv.index("--job") + 1])
    config = job["config"]
    base_url = config["base_url"]
    project_key = config["project_key"]
    severities = ",".join(["HIGH", "BLOCKER"])
    token = os.environ.get("SONAR_TOKEN")
    issues = []
    page = 1
    while True:
        url = (f"{base_url}/api/issues/search?componentKeys={project_key}"
               f"&issueStatuses=OPEN,CONFIRMED&impactSeverities={severities}&ps=100&p={page}")
        _status, body, _error = fetch("GET", url, token)
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
