"""adapters/skills.py against locally built tar.gz archives served by file://
URLs (no network): include filtering, wrapped command files, a stable tree
hash, refusal of a mismatched pin, --update-hashes, `verify`, and refusal of
unsafe archive members."""

import contextlib
import hashlib
import io
import json
import os
import sys
import tarfile
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "adapters"))
import skills  # noqa: E402

REV = "a" * 40
OTHER_REV = "b" * 40
TOP = f"owner-name-{REV}"
SKILL_MD = "---\nname: vitest\ndescription: Vitest helpers\n---\n\n# Vitest\nbody\n"
COMMAND_MD = ("---\nallowed-tools: Bash(git:*)\ndescription: old words\n---\n\n"
              "You are a reviewer.\n\n# Security Review\n\nLook for bugs.\n")
FILES = {
    "skills/vitest/SKILL.md": SKILL_MD,
    "skills/vitest/references/api.md": "api\n",
    "skills/vitest/references/deep/mock.md": "mock\n",
    "skills/vitest/scripts/run.sh": "echo never run\n",
    "skills/vitest/agents/helper.md": "agent\n",
    "skills/other/SKILL.md": "---\nname: other\ndescription: x\n---\n",
    "README.md": "readme\n",
    ".claude/commands/security-review.md": COMMAND_MD,
}
PIN = {"repo": "owner/name", "rev": REV, "path": "skills/vitest",
       "include": ["SKILL.md", "references/**"], "roles": ["red"]}


def archive(files=None, extra=(), prefix=TOP, order=None):
    """A codeload-shaped tar.gz: one top directory, regular files from
    `files`, then the TarInfo/data pairs in `extra`."""
    files = FILES if files is None else files
    names = list(files) if order is None else order
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        top = tarfile.TarInfo(prefix)
        top.type = tarfile.DIRTYPE
        top.mode = 0o755
        tar.addfile(top)
        for name in names:
            data = files[name].encode()
            info = tarfile.TarInfo(f"{prefix}/{name}")
            info.size = len(data)
            info.mtime = hash(name) % 1000
            tar.addfile(info, io.BytesIO(data))
        for info, data in extra:
            tar.addfile(info, io.BytesIO(data) if data is not None else None)
    return buf.getvalue()


def member(name, kind, linkname="", data=b""):
    info = tarfile.TarInfo(name)
    info.type = kind
    info.linkname = linkname
    info.size = len(data) if kind == tarfile.REGTYPE else 0
    return info, data


class SkillsTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        self.cache = os.path.join(self.tmp, "cache")
        self.profile = os.path.join(self.tmp, "project.json")
        self.url = "file://" + os.path.join(self.tmp, "src.tar.gz")

    def serve(self, data):
        with open(os.path.join(self.tmp, "src.tar.gz"), "wb") as fh:
            fh.write(data)

    def write_profile(self, **pins):
        pins = pins or {"vitest": dict(PIN)}
        with open(self.profile, "w", encoding="utf-8") as fh:
            json.dump({"version": 1, "name": "p", "skills": pins}, fh)

    def pins(self):
        with open(self.profile, encoding="utf-8") as fh:
            return json.load(fh)["skills"]

    def sync(self, update=False):
        return skills.sync(self.profile, self.cache, update, url=self.url)

    def refused(self, data, pin=None, name="vitest"):
        self.serve(data)
        self.write_profile(**{name: pin or dict(PIN)})
        with self.assertRaises(skills.SkillError) as caught:
            self.sync(update=True)
        self.assertEqual(os.listdir(self.cache) if os.path.isdir(self.cache) else [], [])
        return str(caught.exception)

    def test_only_the_included_files_under_the_path_are_kept(self):
        self.serve(archive())
        self.write_profile()
        (result,) = self.sync(update=True)
        self.assertEqual(result["files"], ["SKILL.md", "references/api.md",
                                           "references/deep/mock.md"])
        skill = os.path.join(self.cache, f"vitest@{REV}", "vitest")
        self.assertEqual(sorted(os.listdir(os.path.join(self.cache, f"vitest@{REV}"))), ["vitest"])
        self.assertEqual(sorted(os.listdir(skill)), ["SKILL.md", "references"])
        with open(os.path.join(skill, "SKILL.md"), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), SKILL_MD)

    def test_the_root_path_and_literal_globs_follow_the_workspace_matcher(self):
        files = {"SKILL.md": SKILL_MD, "docs/SKILL.md": "nested\n", "x.txt": "x\n"}
        self.serve(archive(files))
        pin = dict(PIN, path=".", include=["SKILL.md"])
        self.write_profile(vitest=pin)
        (result,) = self.sync(update=True)
        self.assertEqual(result["files"], ["SKILL.md"])

    def test_a_command_file_is_wrapped_into_a_skill(self):
        self.serve(archive())
        pin = {"repo": "owner/name", "rev": REV, "path": ".claude/commands/security-review.md",
               "wrap": True, "roles": ["reviewer"]}
        self.write_profile(**{"security-review-anthropic": pin})
        (result,) = self.sync(update=True)
        self.assertEqual(result["files"], ["SKILL.md"])
        path = os.path.join(self.cache, f"security-review-anthropic@{REV}",
                            "security-review-anthropic", "SKILL.md")
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        self.assertEqual(text, '---\nname: security-review-anthropic\n'
                         'description: "old words"\n---\n\n'
                         "You are a reviewer.\n\n# Security Review\n\nLook for bugs.\n")

    def wrapped_description(self, command):
        self.serve(archive({"c.md": command}))
        pin = {"repo": "owner/name", "rev": REV, "path": "c.md", "wrap": True, "roles": ["red"]}
        self.write_profile(cmd=pin)
        self.sync(update=True)
        with open(os.path.join(self.cache, f"cmd@{REV}", "cmd", "SKILL.md"), encoding="utf-8") as fh:
            return fh.read().split("\n")[2]

    def test_a_wrapped_description_prefers_the_frontmatter_then_a_heading_outside_fences(self):
        fenced = "```\n# Example output\n```\n~~~\n# Also an example\n~~~\n## Real *Heading* ##\ntext\n"
        self.assertEqual(self.wrapped_description(fenced), 'description: "Real *Heading*"')
        self.assertEqual(self.wrapped_description("# C# notes\n"), 'description: "C# notes"')
        both = "---\ndescription: Does the review\n---\n# Vuln 1\n"
        self.assertEqual(self.wrapped_description(both), 'description: "Does the review"')
        block = "---\ndescription: |\n  multi\n---\n# Title\n"
        self.assertEqual(self.wrapped_description(block), 'description: "Title"')

    def test_a_command_file_without_a_heading_or_description_is_refused(self):
        data = archive({"c.md": "just words\n```\n# fenced\n```\n"})
        pin = {"repo": "owner/name", "rev": REV, "path": "c.md", "wrap": True, "roles": ["red"]}
        self.assertIn("no heading", self.refused(data, pin))

    def test_the_tree_hash_is_over_sorted_paths_and_file_hashes(self):
        root = os.path.join(self.tmp, "tree")
        os.makedirs(os.path.join(root, "b"))
        for rel, text in (("z.md", "z"), ("a.md", "a"), ("b/c.md", "c")):
            with open(os.path.join(root, rel), "w") as fh:
                fh.write(text)
        lines = "".join(f"{rel}\0{hashlib.sha256(text.encode()).hexdigest()}\n"
                        for rel, text in (("a.md", "a"), ("b/c.md", "c"), ("z.md", "z")))
        self.assertEqual(skills.tree_sha256(root), hashlib.sha256(lines.encode()).hexdigest())

    def test_the_hash_does_not_depend_on_archive_order_or_timestamps(self):
        self.write_profile()
        self.serve(archive())
        first = self.sync(update=True)[0]["sha256"]
        self.serve(archive(order=list(reversed(list(FILES)))))
        os.rename(self.cache, self.cache + ".old")
        self.assertEqual(self.sync(update=True)[0]["sha256"], first)
        self.assertEqual(self.pins()["vitest"]["sha256"], first)

    def test_a_cached_verified_skill_is_not_fetched_again(self):
        self.serve(archive())
        self.write_profile()
        self.assertTrue(self.sync(update=True)[0]["fetched"])
        os.unlink(os.path.join(self.tmp, "src.tar.gz"))
        (again,) = self.sync()
        self.assertFalse(again["fetched"])
        self.assertEqual(again["files"], ["SKILL.md", "references/api.md",
                                          "references/deep/mock.md"])

    def test_a_pin_the_fetched_tree_misses_is_refused_and_the_cache_kept(self):
        self.serve(archive())
        self.write_profile()
        good = self.sync(update=True)[0]["sha256"]
        mount = os.path.join(self.cache, f"vitest@{REV}")
        with open(os.path.join(mount, "vitest", "SKILL.md"), "a") as fh:
            fh.write("tampered\n")
        tampered = skills.tree_sha256(os.path.join(mount, "vitest"))
        self.write_profile(vitest=dict(PIN, sha256="0" * 64))
        with self.assertRaises(skills.SkillError) as caught:
            self.sync()
        self.assertIn(good, str(caught.exception))
        self.assertIn("0" * 64, str(caught.exception))
        self.assertEqual(skills.tree_sha256(os.path.join(mount, "vitest")), tampered)
        self.assertEqual([n for n in os.listdir(self.cache) if n.startswith(".sync-")], [])
        self.assertEqual(self.pins()["vitest"]["sha256"], "0" * 64)

    def test_a_drifted_cache_is_replaced_by_the_pinned_tree(self):
        self.serve(archive())
        self.write_profile()
        good = self.sync(update=True)[0]["sha256"]
        skill = os.path.join(self.cache, f"vitest@{REV}", "vitest")
        with open(os.path.join(skill, "SKILL.md"), "a") as fh:
            fh.write("tampered\n")
        (result,) = self.sync()
        self.assertTrue(result["fetched"])
        self.assertEqual(skills.tree_sha256(skill), good)

    def test_a_missing_pin_needs_update_hashes(self):
        self.serve(archive())
        self.write_profile()
        with self.assertRaises(skills.SkillError) as caught:
            self.sync()
        self.assertIn("--update-hashes", str(caught.exception))
        self.assertFalse(os.path.exists(os.path.join(self.cache, f"vitest@{REV}")))

    def test_update_hashes_rewrites_the_pins_and_keeps_the_rest(self):
        self.serve(archive())
        with open(self.profile, "w", encoding="utf-8") as fh:
            json.dump({"version": 1, "name": "p", "test_command": ["python3", "-m", "unittest"],
                       "skills": {"vitest": dict(PIN, sha256="1" * 64)}}, fh)
        (result,) = self.sync(update=True)
        with open(self.profile, encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertEqual(data["skills"]["vitest"], dict(PIN, sha256=result["sha256"]))
        self.assertNotEqual(result["sha256"], "1" * 64)
        self.assertEqual(data["test_command"], ["python3", "-m", "unittest"])
        self.assertEqual(self.sync()[0]["sha256"], result["sha256"])

    def test_the_profile_is_untouched_when_nothing_changes_or_a_pin_fails(self):
        self.serve(archive())
        self.write_profile()
        self.sync(update=True)
        with open(self.profile, "rb") as fh:
            before = fh.read()
        self.sync(update=True)
        with open(self.profile, "rb") as fh:
            self.assertEqual(fh.read(), before)

    def test_unsafe_member_names_are_refused(self):
        for name in (f"{TOP}/skills/vitest/../../evil", "/etc/passwd",
                     f"{TOP}/skills/vitest/references/..", f"{TOP}\\evil"):
            with self.subTest(name=name):
                data = archive(extra=[member(name, tarfile.REGTYPE, data=b"x")])
                self.assertIn("unsafe archive member name", self.refused(data))

    def test_links_and_devices_on_the_skill_path_are_refused(self):
        base = f"{TOP}/skills/vitest"
        under = {
            "symlink in references": member(f"{base}/references/link.md",
                                            tarfile.SYMTYPE, "/etc/passwd"),
            "hardlink in references": member(f"{base}/references/hard.md",
                                             tarfile.LNKTYPE, f"{TOP}/README.md"),
            "device in references": member(f"{base}/references/dev", tarfile.CHRTYPE),
            "block device in references": member(f"{base}/references/blk", tarfile.BLKTYPE),
            "fifo in references": member(f"{base}/references/fifo", tarfile.FIFOTYPE),
            "SKILL.md is a link": member(f"{base}/SKILL.md", tarfile.SYMTYPE, "../other/SKILL.md"),
        }
        on_path = {
            "the skill dir is a link": member(base, tarfile.SYMTYPE, "../other"),
            "a parent of the path is a link": member(f"{TOP}/skills", tarfile.SYMTYPE, "elsewhere"),
        }
        without_skills = {k: v for k, v in FILES.items() if not k.startswith("skills/")}
        for label, extra in under.items():
            with self.subTest(label):
                files = without_skills if label.startswith("SKILL.md") else None
                self.assertIn("link, device or other special entry",
                              self.refused(archive(files, extra=[extra])))
        for label, extra in on_path.items():
            with self.subTest(label):
                self.assertIn("link, device or other special entry",
                              self.refused(archive(without_skills, extra=[extra])))

    def test_a_link_elsewhere_in_the_repo_is_never_read(self):
        extra = [member(f"{TOP}/CLAUDE.md", tarfile.SYMTYPE, "README.md"),
                 member(f"{TOP}/skills/vitest/scripts/link", tarfile.SYMTYPE, "/etc/passwd"),
                 member(f"{TOP}/skills/other/dev", tarfile.CHRTYPE)]
        self.serve(archive(extra=extra))
        self.write_profile()
        (result,) = self.sync(update=True)
        self.assertEqual(result["files"], ["SKILL.md", "references/api.md",
                                           "references/deep/mock.md"])

    def test_the_skill_must_have_a_description_and_its_own_name(self):
        for text, message in (("# no frontmatter\n", "no frontmatter description"),
                              ("---\nname: vitest\n---\n", "no frontmatter description"),
                              ("---\nname: other\ndescription: d\n---\n", "names itself 'other'")):
            with self.subTest(text=text):
                data = archive({"skills/vitest/SKILL.md": text})
                self.assertIn(message, self.refused(data))
        data = archive({"skills/vitest/references/a.md": "a\n"})
        self.assertIn("no SKILL.md matched", self.refused(data))

    def test_an_archive_with_nothing_matching_or_two_roots_is_refused(self):
        self.assertIn("nothing under", self.refused(
            archive({"skills/vitest/notes.txt": "n\n"}), dict(PIN, include=["*.md"])))
        two = archive(extra=[member("second-root/x", tarfile.REGTYPE, data=b"x")])
        self.assertIn("more than one top-level entry", self.refused(two))

    def test_a_failed_download_or_garbage_is_a_skill_error(self):
        self.write_profile()
        with self.assertRaises(skills.SkillError) as caught:
            self.sync(update=True)
        self.assertIn("cannot fetch", str(caught.exception))
        self.assertIn("unreadable archive", self.refused(b"not a tarball at all"))

    def test_malformed_pins_are_refused_before_any_fetch(self):
        for pin in (dict(PIN, repo="no-slash"), dict(PIN, rev="main"), dict(PIN, rev="A" * 40),
                    dict(PIN, path="../x"), dict(PIN, path="/abs"), dict(PIN, include=[]),
                    dict(PIN, sha256="short"), dict(PIN, wrap="yes")):
            with self.subTest(pin=pin):
                self.write_profile(vitest=pin)
                with self.assertRaises(skills.SkillError):
                    self.sync(update=True)
        self.write_profile(**{"../escape": dict(PIN)})
        with self.assertRaises(skills.SkillError):
            self.sync(update=True)

    def test_verify_returns_the_mount_dir_or_says_to_sync(self):
        self.serve(archive())
        self.write_profile()
        (result,) = self.sync(update=True)
        mount = skills.verify(self.cache, "vitest", REV, result["sha256"])
        self.assertEqual(mount, os.path.join(self.cache, f"vitest@{REV}"))
        with self.assertRaises(skills.SkillError) as caught:
            skills.verify(self.cache, "vitest", OTHER_REV, result["sha256"])
        self.assertIn("adapters/skills.py sync", str(caught.exception))
        with self.assertRaises(skills.SkillError) as caught:
            skills.verify(self.cache, "vitest", REV, "0" * 64)
        self.assertIn("hashes to", str(caught.exception))
        with self.assertRaises(skills.SkillError):
            skills.verify(self.cache, "../vitest", REV, result["sha256"])

    def test_verify_refuses_extras_and_links_in_the_mount(self):
        self.serve(archive())
        self.write_profile()
        (result,) = self.sync(update=True)
        mount = os.path.join(self.cache, f"vitest@{REV}")
        skill = os.path.join(mount, "vitest")
        os.mkdir(os.path.join(mount, "intruder"))
        with self.assertRaises(skills.SkillError):
            skills.verify(self.cache, "vitest", REV, result["sha256"])
        os.rmdir(os.path.join(mount, "intruder"))
        os.symlink("/etc/passwd", os.path.join(skill, "link"))
        with self.assertRaises(skills.SkillError):
            skills.verify(self.cache, "vitest", REV, result["sha256"])
        os.unlink(os.path.join(skill, "link"))
        with open(os.path.join(skill, "extra.md"), "w") as fh:
            fh.write("added\n")
        with self.assertRaises(skills.SkillError):
            skills.verify(self.cache, "vitest", REV, result["sha256"])

    def test_the_command_line_syncs_and_reports(self):
        self.serve(archive())
        self.write_profile()
        out = io.StringIO()
        original = skills.URL
        skills.URL = self.url
        try:
            with contextlib.redirect_stdout(out):
                code = skills.main(["sync", "--profile-json", self.profile,
                                    "--cache", self.cache, "--update-hashes"])
        finally:
            skills.URL = original
        self.assertEqual(code, 0)
        sha = self.pins()["vitest"]["sha256"]
        self.assertIn(f"synced vitest@{REV} sha256={sha}", out.getvalue())
        self.assertIn("  references/api.md", out.getvalue())
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = skills.main(["sync", "--profile-json", os.path.join(self.tmp, "none.json"),
                                "--cache", self.cache])
        self.assertEqual(code, 1)
        self.assertTrue(err.getvalue().startswith("error: "))


if __name__ == "__main__":
    unittest.main()
