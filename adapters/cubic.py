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
            with open(option("--out"), "w") as handle:
                json.dump({"findings": []}, handle)
            return 0
    config = job.get("config", {})
    env = dict(os.environ, CUBIC_DISABLE_AUTOUPDATE="1", CUBIC_DISABLE_LSP_DOWNLOAD="1")
    repo = tempfile.mkdtemp()
    add_code, _out, _err = run(["git", "worktree", "add", "--detach", repo, commit], option("--repo"), env)
    if add_code != 0:
        return 1
    branch_code, _out, _err = run(["git", "branch", BASE_BRANCH, recorded or config.get("since")], repo, env)
    if branch_code != 0:
        return 1
    code, stdout, _stderr = run(
        [config.get("cubic", "cubic"), "review", "--base", BASE_BRANCH, "--json"],
        repo,
        env,
    )
    run(["git", "branch", "-D", BASE_BRANCH], repo, env)
    run(["git", "worktree", "remove", "--force", repo], option("--repo"), env)
    if code not in (0, 1):
        return 1
    findings = [_finding(issue) for issue in json.loads(stdout)["issues"]]
    with open(option("--out"), "w") as handle:
        json.dump({"findings": findings}, handle)
    os.makedirs(os.path.dirname(last), exist_ok=True)
    with open(last, "w") as handle:
        json.dump({"commit": commit}, handle)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
