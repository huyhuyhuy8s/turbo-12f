#!/usr/bin/env python3
import json
import os
import subprocess
import sys

sys.path.insert(0, os.environ["FAKE_DOCKER_ROOT"])
from t12 import sandbox as sb  # noqa: E402


def log(argv):
    with open(os.environ["FAKE_DOCKER_LOG"], "a") as fh:
        fh.write(json.dumps(argv) + "\n")


def container_env(env):
    return {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), **env}


def main():
    argv = sys.argv[1:]
    log(argv)
    if argv[:1] == ["info"]:
        return 0 if os.environ.get("FAKE_DOCKER_INFO_OK", "1") == "1" else 1
    if argv[:2] == ["image", "inspect"]:
        missing = os.environ.get("FAKE_DOCKER_IMAGE_MISSING", "")
        return 1 if len(argv) > 2 and argv[2] == missing else 0
    if argv[:1] == ["run"]:
        rest, worktree, verify_home, env, image = _parse_run(argv[1:])
        if worktree is None or image is None:
            print("fake-docker: could not parse docker run argv", file=sys.stderr)
            return 125
        preexec = None
        if sb.landlock_available():
            preexec = sb.landlock_preexec(worktree, verify_home or worktree)
        try:
            proc = subprocess.run(
                rest, cwd=worktree, env=container_env(env), preexec_fn=preexec,
                capture_output=False)
        except OSError as exc:
            print(f"fake-docker: cannot launch {' '.join(rest)}: {exc}", file=sys.stderr)
            return 125
        return proc.returncode
    print(f"fake-docker: unsupported invocation: {' '.join(argv)}", file=sys.stderr)
    return 1


def _parse_run(tokens):
    worktree = verify_home = image = None
    env = {}
    i = 0
    mounts = []
    while i < len(tokens):
        token = tokens[i]
        if token == "--rm":
            i += 1
        elif token == "--network":
            i += 2
        elif token == "-v":
            mounts.append(tokens[i + 1])
            i += 2
        elif token == "-w":
            worktree = tokens[i + 1]
            i += 2
        elif token == "--user":
            i += 2
        elif token == "-e":
            key, _, value = tokens[i + 1].partition("=")
            env[key] = value
            i += 2
        else:
            image = token
            return tokens[i + 1:], worktree, _verify_home(mounts, worktree), env, image
    return [], worktree, verify_home, env, image


def _verify_home(mounts, worktree):
    for mount in mounts:
        host = mount.split(":", 1)[0]
        if host != worktree:
            return host
    return None


if __name__ == "__main__":
    raise SystemExit(main())
