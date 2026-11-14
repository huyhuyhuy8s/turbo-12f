"""adapters/workspace.py against real git repositories: the blind worktree
hides files from disk and from `git status`, and the patch it exports
carries edits, new files, binary files and deletions back, but never a
hidden path."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "adapters"))
import workspace  # noqa: E402

HIDDEN = ["tests/**", "**/*.test.ts", "eslint.config.*"]
BIN_OLD = bytes(range(256)) * 4
BIN_NEW = b"\x00\xff\x01\xfe" * 300
SECRET_NAMES = ["a.spec.secret", "deep.test.ts", "top.test.ts", "eslint.config"]

FILES = {
    "src/a.ts": b"export const a = 1;\n",
    "src/b.ts": b"export const b = 2;\n",
    "src/top.test.ts": b"hidden top\n",
    "src/deep/er/deep.test.ts": b"hidden nested\n",
    "top.test.ts": b"hidden at root\n",
    "tests/unit/a.spec.secret": b"hidden under tests\n",
    "tests/x.txt": b"hidden too\n",
    "eslint.config.js": b"hidden config\n",
    "gone.txt": b"to delete\n",
    "blob.bin": BIN_OLD,
    "README.md": b"readme\n",
}


def git(cwd, *args):
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE).stdout.decode()


def put(root, rel, data):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


def get(root, rel):
    with open(os.path.join(root, rel), "rb") as f:
        return f.read()


class WorkspaceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = os.path.join(self.tmp, "repo")
        self.dest = os.path.join(self.tmp, "blind")
        os.makedirs(self.repo)
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "config", "user.email", "t@example.com")
        git(self.repo, "config", "user.name", "t")
        for rel, data in FILES.items():
            put(self.repo, rel, data)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "init")
        self.head = git(self.repo, "rev-parse", "HEAD").strip()

    def blind(self):
        base = workspace.blind_worktree(self.repo, self.dest, HIDDEN)
        self.addCleanup(workspace.remove, self.dest)
        return base

    def registered(self):
        return git(self.repo, "worktree", "list", "--porcelain")

    def test_the_blind_worktree_has_no_hidden_file_on_disk_or_in_git_status(self):
        base = self.blind()
        self.assertEqual(base, self.head)
        self.assertEqual(git(self.dest, "rev-parse", "HEAD").strip(), self.head)
        for rel in ("src/top.test.ts", "src/deep/er/deep.test.ts",
                    "top.test.ts", "tests/unit/a.spec.secret", "tests/x.txt",
                    "eslint.config.js"):
            self.assertFalse(os.path.lexists(os.path.join(self.dest, rel)), rel)
        self.assertFalse(os.path.exists(os.path.join(self.dest, "tests")))
        self.assertFalse(os.path.exists(os.path.join(self.dest, "src/deep")))
        for rel in ("src/a.ts", "src/b.ts", "gone.txt", "blob.bin", "README.md"):
            self.assertEqual(get(self.dest, rel), FILES[rel], rel)
        self.assertEqual(git(self.dest, "status", "--porcelain"), "")
        self.assertEqual(git(self.dest, "diff", "HEAD"), "")
        for name in SECRET_NAMES:
            self.assertNotIn(name, git(self.dest, "status"))
        for rel, data in FILES.items():
            self.assertEqual(get(self.repo, rel), data, "original untouched")

    def test_edits_new_files_and_a_binary_file_round_trip(self):
        base = self.blind()
        put(self.dest, "src/a.ts", b"export const a = 100;\n")
        put(self.dest, "src/new/c.ts", b"export const c = 3;\n")
        put(self.dest, "assets/new.bin", BIN_NEW)
        put(self.dest, "blob.bin", BIN_NEW + BIN_OLD)
        patch = workspace.export_patch(self.dest, base, HIDDEN)
        for name in SECRET_NAMES + ["tests/"]:
            self.assertNotIn(name, patch)
        changed = workspace.apply_patch(self.repo, patch)
        self.assertEqual(sorted(changed), [
            "assets/new.bin", "blob.bin", "src/a.ts", "src/new/c.ts"])
        self.assertEqual(get(self.repo, "src/a.ts"), b"export const a = 100;\n")
        self.assertEqual(get(self.repo, "src/new/c.ts"), b"export const c = 3;\n")
        self.assertEqual(get(self.repo, "assets/new.bin"), BIN_NEW)
        self.assertEqual(get(self.repo, "blob.bin"), BIN_NEW + BIN_OLD)
        self.assertEqual(get(self.repo, "tests/x.txt"), FILES["tests/x.txt"])
        self.assertEqual(git(self.repo, "rev-parse", "HEAD").strip(), self.head)
        self.assertEqual(git(self.repo, "diff", "--cached"), "")

    def test_a_non_utf8_text_file_round_trips(self):
        base = self.blind()
        data = b"caf\xe9 \xff\xfe latin-1\nline two\n"
        put(self.dest, "README.md", data)
        patch = workspace.export_patch(self.dest, base, HIDDEN)
        workspace.apply_patch(self.repo, patch)
        self.assertEqual(get(self.repo, "README.md"), data)

    def test_deleting_a_visible_file_round_trips_and_hidden_files_stay(self):
        base = self.blind()
        os.remove(os.path.join(self.dest, "gone.txt"))
        patch = workspace.export_patch(self.dest, base, HIDDEN)
        self.assertIn("deleted file mode", patch)
        self.assertEqual(workspace.apply_patch(self.repo, patch), ["gone.txt"])
        self.assertFalse(os.path.exists(os.path.join(self.repo, "gone.txt")))
        for rel in FILES:
            if rel != "gone.txt":
                self.assertEqual(get(self.repo, rel), FILES[rel], rel)

    def test_no_change_exports_an_empty_patch(self):
        base = self.blind()
        patch = workspace.export_patch(self.dest, base, HIDDEN)
        self.assertEqual(patch, "")
        self.assertEqual(workspace.apply_patch(self.repo, patch), [])

    def test_creating_a_hidden_path_is_refused(self):
        for rel in ("tests/new.txt", "src/brand.test.ts", "src/deep/er/x/y.test.ts",
                    "eslint.config.mjs"):
            with self.subTest(rel=rel):
                dest = os.path.join(self.tmp, "blind-" + rel.replace("/", "_"))
                base = workspace.blind_worktree(self.repo, dest, HIDDEN)
                self.addCleanup(workspace.remove, dest)
                put(dest, "src/ok.ts", b"fine\n")
                put(dest, rel, b"smuggled\n")
                with self.assertRaises(workspace.WorkspaceError) as ctx:
                    workspace.export_patch(dest, base, HIDDEN)
                self.assertIn(rel, str(ctx.exception))

    def test_recreating_or_editing_a_hidden_path_is_refused(self):
        base = self.blind()
        put(self.dest, "src/top.test.ts", b"my own version\n")
        with self.assertRaises(workspace.WorkspaceError) as ctx:
            workspace.export_patch(self.dest, base, HIDDEN)
        self.assertIn("src/top.test.ts", str(ctx.exception))

    def test_a_tracked_hidden_path_brought_back_into_git_is_refused(self):
        base = self.blind()
        git(self.dest, "update-index", "--no-skip-worktree", "tests/x.txt")
        put(self.dest, "tests/x.txt", b"changed\n")
        with self.assertRaises(workspace.WorkspaceError):
            workspace.export_patch(self.dest, base, HIDDEN)

    def test_hidden_deletions_never_reach_the_patch(self):
        base = self.blind()
        git(self.dest, "update-index", "--no-skip-worktree", "tests/x.txt")
        put(self.dest, "src/a.ts", b"edited\n")
        patch = workspace.export_patch(self.dest, base, HIDDEN)
        self.assertNotIn("tests/x.txt", patch)
        self.assertEqual(workspace.apply_patch(self.repo, patch), ["src/a.ts"])

    def test_a_dirty_source_worktree_is_refused(self):
        put(self.repo, "src/a.ts", b"uncommitted\n")
        with self.assertRaises(workspace.WorkspaceError):
            workspace.blind_worktree(self.repo, self.dest, HIDDEN)
        self.assertFalse(os.path.exists(self.dest))
        git(self.repo, "checkout", "--", "src/a.ts")
        put(self.repo, "untracked.txt", b"x\n")
        with self.assertRaises(workspace.WorkspaceError):
            workspace.blind_worktree(self.repo, self.dest, HIDDEN)

    def test_a_conflicting_apply_raises_and_changes_nothing(self):
        base = self.blind()
        put(self.dest, "src/a.ts", b"export const a = 100;\n")
        put(self.dest, "src/b.ts", b"export const b = 200;\n")
        patch = workspace.export_patch(self.dest, base, HIDDEN)
        put(self.repo, "src/b.ts", b"export const b = 'someone else';\n")
        with self.assertRaises(workspace.WorkspaceError):
            workspace.apply_patch(self.repo, patch)
        self.assertEqual(get(self.repo, "src/a.ts"), FILES["src/a.ts"])
        self.assertEqual(get(self.repo, "src/b.ts"),
                         b"export const b = 'someone else';\n")

    def test_remove_leaves_no_worktree_registered(self):
        self.blind()
        self.assertIn(os.path.realpath(self.dest), self.registered())
        workspace.remove(self.dest)
        self.assertFalse(os.path.exists(self.dest))
        self.assertNotIn("blind", self.registered())
        self.assertEqual(git(self.repo, "worktree", "list").count("\n"), 1)
        workspace.remove(self.dest)

    def test_remove_works_after_the_worker_dirtied_the_worktree(self):
        self.blind()
        put(self.dest, "src/new.ts", b"x\n")
        os.remove(os.path.join(self.dest, "gone.txt"))
        workspace.remove(self.dest)
        self.assertEqual(git(self.repo, "worktree", "list").count("\n"), 1)

    def test_a_failed_blind_worktree_leaves_nothing_registered(self):
        with self.assertRaises(workspace.WorkspaceError):
            workspace.blind_worktree(os.path.join(self.tmp, "nope"),
                                     self.dest, HIDDEN)
        self.assertEqual(git(self.repo, "worktree", "list").count("\n"), 1)


class GlobTest(unittest.TestCase):
    def test_the_documented_glob_rules(self):
        match = workspace._matcher(["tests/**", "**/*.test.ts", "eslint.config.*"])
        for path in ("tests/a", "tests/a/b/c.txt", "a.test.ts", "x/y/z.test.ts",
                     "eslint.config.js"):
            self.assertTrue(match(path), path)
        for path in ("src/a.ts", "test/a", "mytests/a", "tests", "a.test.tsx",
                     "src/eslint.config"):
            self.assertFalse(match(path), path)


if __name__ == "__main__":
    unittest.main()
