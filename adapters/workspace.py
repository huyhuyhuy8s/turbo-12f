#!/usr/bin/env python3
"""Blind worktrees: give a worker the repo minus the files it must not see.

Serves three of the 12 factors. Factor 3 (own your context window): the
worker's view of the repo is built here, not discovered by the model, and
the hidden files (the red tests, the lint config) are not on its disk at
all. Factor 5 (unify execution state): the worker's whole effect on the
repo is one patch, a plain string the factory can store, replay and
apply. Factor 10 (small, focused agents): a worker edits a throwaway
worktree and cannot touch the original until the factory applies its
patch.

`blind_worktree` makes a detached git worktree of HEAD and takes every
tracked file that matches the hidden globs off the disk. Each is also
marked skip-worktree in the worktree's own index, so `git status` and
`git diff` there stay quiet: no deleted names for the worker to read. The
original worktree and HEAD are untouched, and nothing is committed.

`export_patch` stages the worktree in its own index and diffs it against
the base commit with `git diff --binary`, so new files and binary files
are carried. It refuses the patch when a hidden path was created or
changed, whatever the cause.

`apply_patch` applies the patch to a worktree's files, all or nothing,
without committing.

Globs are matched with fnmatch rules on repo-relative POSIX paths, with
two additions: `**/` matches any directory prefix, including none, so
`**/*.test.ts` also matches `a.test.ts`; and `dir/**` matches everything
under `dir`. `*` also crosses `/`, as in `fnmatch`, so a hidden pattern
errs on the side of hiding more.

Host-side, like the other adapters: git through subprocess, stdlib only.
"""

import os
import re
import subprocess

GIT_ENV_DROP = (
    "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR",
    "GIT_PREFIX", "GIT_OBJECT_DIRECTORY", "GIT_NAMESPACE",
)


class WorkspaceError(Exception):
    """A workspace step failed, or a patch broke the hidden-path rule."""


def _git(cwd, *args, input=None):
    """Run git in `cwd`; stdout as bytes. Raise WorkspaceError on failure."""
    env = {k: v for k, v in os.environ.items() if k not in GIT_ENV_DROP}
    try:
        proc = subprocess.run(
            ["git", *args], cwd=cwd, env=env, input=input,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError as e:
        raise WorkspaceError(f"git {args[0]}: {e}") from e
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace").strip()
        raise WorkspaceError(f"git {' '.join(args)}: {err}")
    return proc.stdout


def _paths(raw):
    """Split NUL-separated git output into repo-relative paths."""
    return [p.decode("utf-8", "surrogateescape")
            for p in raw.split(b"\0") if p]


def _nul(paths):
    return b"".join(p.encode("utf-8", "surrogateescape") + b"\0"
                    for p in paths)


def _glob_regex(glob):
    out, i, n = [], 0, len(glob)
    while i < n:
        if glob.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif glob.startswith("**", i):
            out.append(".*")
            i += 2
        elif glob[i] == "*":
            out.append(".*")
            i += 1
        elif glob[i] == "?":
            out.append(".")
            i += 1
        elif glob[i] == "[" and (end := glob.find("]", i + 2)) != -1:
            body = glob[i + 1:end]
            if body[0] == "!":
                body = "^" + body[1:]
            out.append("[" + body.replace("\\", "\\\\") + "]")
            i = end + 1
        else:
            out.append(re.escape(glob[i]))
            i += 1
    return re.compile("".join(out), re.DOTALL)


def _matcher(hidden_globs):
    regexes = [_glob_regex(g) for g in hidden_globs]
    return lambda path: any(r.fullmatch(path) for r in regexes)


def _forget_dirs(root, path):
    """Remove the now-empty parent directories of a deleted `path`."""
    parent = os.path.dirname(os.path.join(root, path))
    root = os.path.abspath(root)
    while os.path.abspath(parent) != root:
        try:
            os.rmdir(parent)
        except OSError:
            return
        parent = os.path.dirname(parent)


def blind_worktree(worktree, dest, hidden_globs) -> str:
    """A detached git worktree of `worktree`'s HEAD at `dest` with every
    tracked file matching hidden_globs gone from disk and invisible to git
    there. Returns the base commit sha. Refuses a dirty `worktree`."""
    if _git(worktree, "status", "--porcelain", "--untracked-files=all"):
        raise WorkspaceError(f"{worktree} has uncommitted changes")
    base = _git(worktree, "rev-parse", "--verify", "HEAD^{commit}") \
        .decode().strip()
    _git(worktree, "worktree", "add", "--detach", dest, base)
    try:
        hidden = _matcher(hidden_globs)
        gone = [p for p in _paths(_git(dest, "ls-files", "-z")) if hidden(p)]
        if gone:
            _git(dest, "update-index", "--skip-worktree", "-z", "--stdin",
                 input=_nul(gone))
            for path in gone:
                os.remove(os.path.join(dest, path))
                _forget_dirs(dest, path)
    except BaseException:
        remove(dest)
        raise
    return base


def export_patch(dest, base, hidden_globs) -> str:
    """Binary-safe `git diff` of dest's working tree (new files included)
    against base. Raises WorkspaceError if a hidden path was created or
    changed in dest. A hidden file's deletion is not part of the patch."""
    hidden = _matcher(hidden_globs)
    touched = [p for p in _paths(_git(dest, "ls-tree", "-r", "-z",
                                      "--name-only", base))
               if hidden(p) and os.path.lexists(os.path.join(dest, p))]
    touched += [p for p in _paths(_git(dest, "ls-files", "-o",
                                       "--exclude-standard", "-z"))
                if hidden(p)]
    _git(dest, "add", "-A")
    changes = _git(dest, "diff", "--cached", "--no-renames", "--name-status",
                   "-z", base).split(b"\0")
    deleted = []
    for status, path in zip(changes[0::2], changes[1::2]):
        path = path.decode("utf-8", "surrogateescape")
        if not hidden(path):
            continue
        if status == b"D":
            deleted.append(path)
        else:
            touched.append(path)
    if touched:
        raise WorkspaceError(
            "hidden paths were created or changed: "
            + ", ".join(sorted(set(touched))))
    patch = _git(dest, "diff", "--cached", "--binary", "--full-index",
                 "--no-renames", "--no-ext-diff", "--no-textconv",
                 "--no-color", "--src-prefix=a/", "--dst-prefix=b/", base,
                 "--", ".", *(f":(exclude,literal){p}" for p in deleted))
    return patch.decode("utf-8", "surrogateescape")


def apply_patch(worktree, patch) -> list[str]:
    """Apply `patch` to `worktree`'s files, without staging or committing.
    All or nothing. Returns the changed paths. Raises WorkspaceError when
    the patch does not apply."""
    if not patch:
        return []
    data = patch.encode("utf-8", "surrogateescape")
    stat = _git(worktree, "apply", "--numstat", "-z", input=data)
    _git(worktree, "apply", "--binary", input=data)
    changed = []
    for record in stat.split(b"\0"):
        if record:
            changed.append(record.split(b"\t", 2)[2]
                           .decode("utf-8", "surrogateescape"))
    return changed


def remove(dest):
    """Delete the worktree at `dest` and leave no registration behind."""
    if not os.path.isdir(dest):
        return
    common = _git(dest, "rev-parse", "--path-format=absolute",
                  "--git-common-dir").decode().strip()
    _git(common, "worktree", "remove", "--force", "--force", dest)
    _git(common, "worktree", "prune")
