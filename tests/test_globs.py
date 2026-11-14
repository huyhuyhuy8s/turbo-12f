"""Glob matching in the core, and parity with the blind worktree's matcher
in adapters/workspace.py, which builds the hidden view from the same globs."""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "adapters"))

from t12 import globs  # noqa: E402
import workspace  # noqa: E402

# (glob, path, matches)
TABLE = [
    ("tests/**", "tests/test_a.py", True),
    ("tests/**", "tests/sub/test_a.py", True),
    ("tests/**", "tests", False),
    ("tests/**", "src/tests/test_a.py", False),
    ("tests/**", "tests_extra/a.py", False),
    ("**/*.test.ts", "a.test.ts", True),
    ("**/*.test.ts", "web/src/a.test.ts", True),
    ("**/*.test.ts", "a.test.tsx", False),
    ("**/tests/**", "tests/a.py", True),
    ("**/tests/**", "pkg/tests/a/b.py", True),
    ("**/tests/**", "pkg/mytests/a.py", False),
    ("*.py", "a.py", True),
    ("*.py", "pkg/a.py", True),
    ("*.py", "a.pyc", False),
    ("test_?.py", "test_a.py", True),
    ("test_?.py", "test_ab.py", False),
    ("test_[ab].py", "test_a.py", True),
    ("test_[ab].py", "test_c.py", False),
    ("test_[!ab].py", "test_c.py", True),
    ("test_[!ab].py", "test_a.py", False),
    ("a.b+c", "a.b+c", True),
    ("a.b+c", "aXb+c", False),
    ("[", "[", True),
    ("conf(1).ini", "conf(1).ini", True),
    ("docs/**/index.md", "docs/index.md", True),
    ("docs/**/index.md", "docs/a/index.md", True),
    ("src/*/x.py", "src/a/b/x.py", True),
]


class MatchTest(unittest.TestCase):
    def test_table(self):
        for glob, path, expected in TABLE:
            self.assertEqual(globs.match(path, [glob]), expected, f"{glob!r} vs {path!r}")

    def test_any_glob_matches(self):
        self.assertTrue(globs.match("spec/a.rb", ["tests/**", "spec/**"]))
        self.assertFalse(globs.match("lib/a.rb", ["tests/**", "spec/**"]))

    def test_no_globs_match_nothing(self):
        self.assertFalse(globs.match("tests/a.py", []))

    def test_regex_is_compiled_and_anchored_by_fullmatch(self):
        regex = globs.regex("tests/**")
        self.assertTrue(regex.fullmatch("tests/a.py"))
        self.assertFalse(regex.fullmatch("x/tests/a.py"))


class WorkspaceParityTest(unittest.TestCase):
    def test_every_pair_matches_as_in_the_blind_worktree(self):
        paths = sorted({p for _, p, _ in TABLE} | {
            "", "tests/", "a/b/c.py", "tests/a b.py", "tests/é.py", "tests/a\nb.py"})
        for glob in sorted({g for g, _, _ in TABLE}):
            theirs = workspace._matcher([glob])
            for path in paths:
                self.assertEqual(globs.match(path, [glob]), bool(theirs(path)),
                                 f"{glob!r} vs {path!r}")

    def test_the_compiled_patterns_are_identical(self):
        for glob, _, _ in TABLE:
            ours, theirs = globs.regex(glob), workspace._glob_regex(glob)
            self.assertEqual((ours.pattern, ours.flags), (theirs.pattern, theirs.flags), glob)


if __name__ == "__main__":
    unittest.main()
