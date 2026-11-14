"""Glob matching for test files and hidden paths, in the core.

The core cannot import `adapters/workspace.py`, which builds the blind
worktree from the same globs, so this is a copy of its semantics and a
parity test (tests/test_globs.py) keeps the two aligned. Globs are
matched against repo-relative POSIX paths: `**/` matches any directory
prefix, including none, so `**/*.test.ts` also matches `a.test.ts`;
`dir/**` matches everything under `dir`; `*` also crosses `/`, so a
glob errs on the side of matching more.
"""

import re


def regex(glob):
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


def match(path, globs):
    """True when `path` matches any of `globs`."""
    return any(regex(g).fullmatch(path) for g in globs)
