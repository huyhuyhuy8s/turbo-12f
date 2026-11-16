import hashlib
import json
import os
import sys
import subprocess
import tempfile


def _default_run(argv, cwd, env):
    done = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True)
    return done.returncode, done.stdout, done.stderr


BASE_BRANCH = "t12-job/cubic-base"
DETAIL_LIMIT = 4000


def _finding(issue):
    key = f"{issue['file']}\n{issue['title']}"
    location = issue["file"] if issue.get("line") is None else f"{issue['file']}:{issue['line']}"
    return {
        "fingerprint": "cubic:" + hashlib.sha256(key.encode()).hexdigest()[:16],
        "title": f"[{issue['priority']}] {issue['title']}",
        "detail": f"{location}\n\n{issue['description'][:DETAIL_LIMIT]}",
        "url": None,
    }


def _write_findings(path, findings):
    with open(path, "w") as handle:
        json.dump({"findings": findings}, handle)


def main(argv=None, run=None):
    run = run or _default_run
    def option(flag):
        return argv[argv.index(flag) + 1]

    job = json.loads(option("--job"))
    commit = option("--commit")
    last = os.path.join(option("--home"), "jobs", job["name"], "cubic-last.json")
    recorded = None
    if os.path.exists(last):
        with open(last) as handle:
            recorded = json.load(handle).get("commit")
        if recorded == commit:
            _write_findings(option("--out"), [])
            return 0
    config = job.get("config", {})
    if not recorded and not config.get("since"):
        return 1
    env = dict(os.environ, CUBIC_DISABLE_AUTOUPDATE="1", CUBIC_DISABLE_LSP_DOWNLOAD="1")
    repo = tempfile.mkdtemp()
    source_repo = option("--repo")
    add_code, _out, _err = run(["git", "worktree", "add", "--detach", repo, commit], source_repo, env)
    if add_code != 0:
        return 1
    branch_code, _out, _err = run(["git", "branch", BASE_BRANCH, recorded or config.get("since")], repo, env)
    if branch_code != 0:
        run(["git", "worktree", "remove", "--force", repo], source_repo, env)
        return 1
    try:
        code, stdout, _stderr = run(
            [config.get("cubic", "cubic"), "review", "--base", BASE_BRANCH, "--json"],
            repo,
            env,
        )
    finally:
        run(["git", "branch", "-D", BASE_BRANCH], repo, env)
        run(["git", "worktree", "remove", "--force", repo], source_repo, env)
    if code not in (0, 1):
        return 1
    try:
        data = json.loads(stdout)
        if "error" in data:
            return 1
        issues = data["issues"]
    except (ValueError, KeyError, TypeError):
        return 1
    if not isinstance(issues, list):
        return 1
    findings = [_finding(issue) for issue in issues]
    _write_findings(option("--out"), findings)
    os.makedirs(os.path.dirname(last), exist_ok=True)
    with open(last, "w") as handle:
        json.dump({"commit": commit}, handle)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
