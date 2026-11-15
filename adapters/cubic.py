import hashlib
import json
import os
import sys
import tempfile


def main(argv=None, run=None):
    def option(flag):
        return argv[argv.index(flag) + 1]

    job = json.loads(option("--job"))
    commit = option("--commit")
    last = os.path.join(option("--home"), "jobs", job["name"], "cubic-last.json")
    if os.path.exists(last):
        with open(last) as handle:
            recorded = json.load(handle).get("commit")
        if recorded == commit:
            with open(option("--out"), "w") as handle:
                json.dump({"findings": []}, handle)
            return 0
    config = job.get("config", {})
    env = dict(os.environ, CUBIC_DISABLE_AUTOUPDATE="1", CUBIC_DISABLE_LSP_DOWNLOAD="1")
    repo = tempfile.mkdtemp()
    run(["git", "worktree", "add", "--detach", repo, commit], option("--repo"), env)
    run(["git", "branch", "t12-job/cubic-base", config.get("since")], repo, env)
    _code, stdout, _stderr = run(
        [config.get("cubic", "cubic"), "review", "--base", "t12-job/cubic-base", "--json"],
        repo,
        env,
    )
    findings = []
    for issue in json.loads(stdout)["issues"]:
        key = f"{issue['file']}\n{issue['title']}"
        findings.append({
            "fingerprint": "cubic:" + hashlib.sha256(key.encode()).hexdigest()[:16],
            "title": f"[{issue['priority']}] {issue['title']}",
            "detail": f"{issue['file']}:{issue['line']}\n\n{issue['description'][:4000]}",
            "url": None,
        })
    with open(option("--out"), "w") as handle:
        json.dump({"findings": findings}, handle)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
