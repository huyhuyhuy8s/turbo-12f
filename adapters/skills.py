#!/usr/bin/env python3
"""Pinned third-party skills: fetch them once, hash them, mount only what matches.

Serves three of the 12 factors. Factor 2 (own your prompts): the skill
text a worker reads is chosen by the project profile (repo, commit, the
files to keep), never by whatever a product repo or a user directory
happens to contain. Factor 3 (own your context window): `include` keeps
a skill's scripts, hooks and agents out of the worker's reach, and a
wrapped command file becomes a skill with generated frontmatter, so what
the worker sees is exactly what is cached here. Factor 8 (tools are
structured outputs): the runner calls `verify` before every step that
mounts a skill, so a cache that drifted from its pin stops the step
instead of changing the prompt.

    python3 adapters/skills.py sync --profile-json .t12/project.json \\
        --cache <dir> [--update-hashes]

For every entry of the profile's `skills` map it downloads
https://codeload.github.com/<repo>/tar.gz/<rev>, keeps the members under
`path` that match `include`, and installs them as
<cache>/<name>@<rev>/<name>/. `<cache>/<name>@<rev>` is the directory the
runner hands to omp as a skills custom directory (omp scans it one level
deep), so it holds that one skill and nothing else. With `wrap`, `path`
is one command file and the skill is a generated SKILL.md: frontmatter
`name` and `description` (the file's own frontmatter description, else
its first heading), then the command text without its own frontmatter.

Nothing from the archive is ever executed, and nothing is extracted by
tarfile: each kept member is a regular file whose bytes are written under
a path this module built. The archive is refused when a member name is
absolute or has a `..` part, and when a link, device or other special
member is selected by `include` or sits on the skill's own path (links
elsewhere in the repo are never read, so they cannot matter). Size caps
bound the download, the unpacked archive and each file.

The synced tree's hash is `tree_sha256`: sha256 over the sorted lines
"<relpath>\\0<sha256 of the file>\\n". A pinned `sha256` that the fetched
tree does not match refuses the sync and leaves the cache untouched;
`--update-hashes` writes the computed hashes into the profile instead
(the way to pin a new rev). `verify` re-hashes a cached skill.

Globs are the blind worktree's: `adapters/workspace.py`'s matcher, so
`references/**` matches everything under references and a literal
`SKILL.md` matches only the root one. Host-side, like the other
adapters: urllib and tarfile, stdlib only. The core never imports it.
"""

import argparse
import gzip
import hashlib
import http.client
import json
import os
import re
import shutil
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
import zlib

import workspace

URL = "https://codeload.github.com/{repo}/tar.gz/{rev}"
MAX_DOWNLOAD = 256 * 1024 * 1024
MAX_UNPACKED = 1024 * 1024 * 1024
MAX_FILE = 8 * 1024 * 1024
MAX_SELECTED = 64 * 1024 * 1024
TIMEOUT_S = 60

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
REV_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
HEADING_RE = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]+(.+?)(?:[ \t]+#+)?[ \t]*$")
FENCE_RE = re.compile(r"^[ \t]{0,3}(```|~~~)")
PIN_KEYS = ("repo", "rev", "path", "include", "wrap")


class SkillError(Exception):
    """A pin is malformed, an archive is unsafe, or a hash does not match."""


# -- the tree hash and the cache ------------------------------------------

def tree_files(root):
    """[(relpath, sha256 hex)] of every file under `root`, sorted. A link or
    any other non-regular entry is an error: a skill dir holds plain files."""
    found = []
    for folder, dirs, files in os.walk(root, followlinks=False):
        for entry in dirs + files:
            full = os.path.join(folder, entry)
            if os.path.islink(full):
                raise SkillError(f"{full}: links are not allowed in a skill")
        for entry in files:
            full = os.path.join(folder, entry)
            if not os.path.isfile(full):
                raise SkillError(f"{full}: not a regular file")
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            with open(full, "rb") as fh:
                found.append((rel, hashlib.sha256(fh.read()).hexdigest()))
    return sorted(found)


def tree_sha256(root):
    """sha256 over the sorted lines "<relpath>\\0<sha256 of file>\\n"."""
    digest = hashlib.sha256()
    for rel, file_hash in tree_files(root):
        digest.update(f"{rel}\0{file_hash}\n".encode("utf-8", "surrogateescape"))
    return digest.hexdigest()


def mount_dir(cache, name, rev):
    """The directory omp scans as a skills custom directory."""
    return os.path.join(cache, f"{name}@{rev}")


def verify(cache, name, rev, sha256):
    """The cached skill's mount directory, `<cache>/<name>@<rev>`, when it
    holds exactly `<name>/` whose tree hashes to `sha256`. Raises SkillError
    otherwise; the message says to run the sync."""
    if not NAME_RE.match(name) or not REV_RE.match(rev):
        raise SkillError(f"skill {name!r}@{rev!r}: not a valid name and commit")
    mount = mount_dir(cache, name, rev)
    fix = "run adapters/skills.py sync"
    if not os.path.isdir(mount) or os.path.islink(mount):
        raise SkillError(f"skill {name}@{rev[:12]} is not in the cache {cache}: {fix}")
    if os.listdir(mount) != [name]:
        raise SkillError(f"skill {name}@{rev[:12]}: {mount} must hold only {name}/: {fix}")
    skill = os.path.join(mount, name)
    if os.path.islink(skill) or not os.path.isdir(skill):
        raise SkillError(f"skill {name}@{rev[:12]}: {skill} is not a directory: {fix}")
    got = tree_sha256(skill)
    if got != sha256:
        raise SkillError(f"skill {name}@{rev[:12]} in {cache} hashes to {got}, "
                         f"pinned {sha256}: {fix}")
    return mount


# -- the archive -----------------------------------------------------------

class _Limited:
    """A read-only stream that fails once more than `limit` bytes pass."""

    def __init__(self, stream, limit, what):
        self.stream, self.limit, self.what, self.count = stream, limit, what, 0

    def read(self, size=-1):
        data = self.stream.read(size)
        self.count += len(data)
        if self.count > self.limit:
            raise SkillError(f"{self.what} is larger than {self.limit} bytes")
        return data


def _parts(name):
    """The path components of a tar member name, refusing absolute names and
    `..`. `.` and empty parts are dropped."""
    if name.startswith("/") or "\\" in name or "\0" in name:
        raise SkillError(f"unsafe archive member name {name!r}")
    parts = name.split("/")
    if ".." in parts:
        raise SkillError(f"unsafe archive member name {name!r}")
    return [p for p in parts if p not in ("", ".")]


def _check_pin(name, pin):
    where = f"skill {name!r}"
    if not NAME_RE.match(name):
        raise SkillError(f"{where}: name must match {NAME_RE.pattern}")
    if not isinstance(pin, dict):
        raise SkillError(f"{where}: must be an object")
    if not isinstance(pin.get("repo"), str) or not REPO_RE.match(pin["repo"]):
        raise SkillError(f"{where}: repo must look like owner/name")
    if not isinstance(pin.get("rev"), str) or not REV_RE.match(pin["rev"]):
        raise SkillError(f"{where}: rev must be a 40-hex commit sha")
    path = pin.get("path")
    if (not isinstance(path, str) or not path or path.startswith("/")
            or "\\" in path or ".." in path.split("/")):
        raise SkillError(f"{where}: path must be relative, without '..'")
    wrap = pin.get("wrap", False)
    include = pin.get("include", [])
    if not isinstance(wrap, bool):
        raise SkillError(f"{where}: wrap must be true or false")
    if (not isinstance(include, list) or not all(isinstance(g, str) and g for g in include)
            or (not wrap and not include)):
        raise SkillError(f"{where}: include must be a list of globs (not empty unless wrap)")
    if wrap and not _parts(path):
        raise SkillError(f"{where}: a wrapped skill's path must be a file")
    sha256 = pin.get("sha256")
    if sha256 is not None and (not isinstance(sha256, str) or not SHA256_RE.match(sha256)):
        raise SkillError(f"{where}: sha256 must be 64 hex digits")
    return {"repo": pin["repo"], "rev": pin["rev"], "path": _parts(path),
            "include": list(include), "wrap": wrap, "sha256": sha256}


def _select(stream, pin):
    """{relpath: bytes} of the archive members `pin` keeps. See the module
    docstring for what is refused."""
    path = pin["path"]
    keep = workspace._matcher(pin["include"])
    chosen, total, prefix = {}, 0, None
    with tarfile.open(fileobj=stream, mode="r|") as tar:
        for member in tar:
            parts = _parts(member.name)
            if not parts:
                continue
            if prefix is None:
                prefix = parts[0]
            elif parts[0] != prefix:
                raise SkillError(f"archive has more than one top-level entry: "
                                 f"{prefix!r} and {parts[0]!r}")
            rest = parts[1:]
            related = rest[:len(path)] == path[:len(rest)]
            if not related:
                continue
            sub = rest[len(path):]
            selected = (rest == path) if pin["wrap"] else (
                len(rest) >= len(path) and keep("/".join(sub)))
            on_path = len(rest) <= len(path) and rest == path[:len(rest)]
            if not (member.isreg() or member.isdir()) and (selected or on_path):
                raise SkillError(f"archive member {member.name!r} is a link, device or "
                                 "other special entry on or under the skill path")
            if not member.isreg() or not selected:
                continue
            rel = "/".join(sub) if sub else path[-1]
            if any(ord(c) < 32 for c in rel):
                raise SkillError(f"archive member {member.name!r}: control character in name")
            if rel in chosen:
                raise SkillError(f"archive member {member.name!r} appears twice")
            if member.size > MAX_FILE:
                raise SkillError(f"archive member {member.name!r} is larger than {MAX_FILE} bytes")
            total += member.size
            if total > MAX_SELECTED:
                raise SkillError(f"the selected files are larger than {MAX_SELECTED} bytes")
            chosen[rel] = tar.extractfile(member).read()
    return chosen


def _frontmatter(text):
    """(top-level frontmatter keys, body). No frontmatter gives ({}, text)."""
    match = re.match(r"---[ \t]*\r?\n(.*?\r?\n)?---[ \t]*(?:\r?\n|$)", text, re.DOTALL)
    if not match:
        return {}, text
    keys = {}
    for line in (match.group(1) or "").splitlines():
        key, sep, value = line.partition(":")
        if sep and key and not key[0].isspace() and not key.startswith("#"):
            keys[key.strip()] = value.strip().strip("\"'")
    return keys, text[match.end():]


def _first_heading(body):
    """The text of the first markdown heading outside a code fence."""
    fence = None
    for line in body.splitlines():
        marker = FENCE_RE.match(line)
        if marker:
            fence = None if fence == marker.group(1) else (fence or marker.group(1))
        elif fence is None:
            heading = HEADING_RE.match(line)
            if heading:
                return heading.group(1)
    return None


def _wrapped(name, data):
    """SKILL.md for a command file: generated frontmatter, then its body. The
    description is the one in the file's own frontmatter when it has a
    one-line one (a command file's headings are often examples inside its
    prompt), else its first heading outside a code fence."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SkillError(f"skill {name!r}: the command file is not UTF-8: {exc}") from exc
    meta, body = _frontmatter(text)
    description = meta.get("description")
    if not description or description[0] in "|>":
        description = _first_heading(body)
    if description is None:
        raise SkillError(f"skill {name!r}: the command file has no heading or "
                         "frontmatter description to describe it")
    return (f"---\nname: {name}\ndescription: {json.dumps(description, ensure_ascii=False)}\n"
            f"---\n\n{body.lstrip(chr(10))}").encode("utf-8")


def _check_skill_md(name, files):
    if "SKILL.md" not in files:
        raise SkillError(f"skill {name!r}: no SKILL.md matched under its path")
    try:
        meta, _ = _frontmatter(files["SKILL.md"].decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise SkillError(f"skill {name!r}: SKILL.md is not UTF-8: {exc}") from exc
    if not meta.get("description"):
        raise SkillError(f"skill {name!r}: SKILL.md has no frontmatter description")
    if meta.get("name", name) != name:
        raise SkillError(f"skill {name!r}: SKILL.md names itself {meta['name']!r}")


def build(name, pin, stream, dest):
    """Write the skill into `dest` (a new directory) from the tar.gz `stream`;
    returns its sorted relative file list."""
    stream = _Limited(gzip.GzipFile(fileobj=_Limited(stream, MAX_DOWNLOAD, "the download")),
                      MAX_UNPACKED, "the unpacked archive")
    try:
        files = _select(stream, pin)
    except (tarfile.TarError, OSError, EOFError, zlib.error, http.client.HTTPException) as exc:
        raise SkillError(f"skill {name!r}: unreadable archive: {exc}") from exc
    if pin["wrap"]:
        if not files:
            raise SkillError(f"skill {name!r}: {'/'.join(pin['path'])} is not in the archive")
        (data,) = files.values()
        files = {"SKILL.md": _wrapped(name, data)}
    if not files:
        raise SkillError(f"skill {name!r}: nothing under {'/'.join(pin['path']) or '.'} "
                         f"matched {pin['include']}")
    _check_skill_md(name, files)
    root = os.path.join(dest, name)
    try:
        for rel, data in files.items():
            target = os.path.join(root, *rel.split("/"))
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "wb") as fh:
                fh.write(data)
    except OSError as exc:
        raise SkillError(f"skill {name!r}: cannot lay out {rel!r}: {exc}") from exc
    return sorted(files)


# -- sync ------------------------------------------------------------------

def fetch_into(name, pin, cache, url=URL):
    """Download and build one skill in a scratch dir under `cache`. Returns
    (scratch dir, files, tree hash); the caller installs or removes it."""
    os.makedirs(cache, exist_ok=True)
    scratch = tempfile.mkdtemp(prefix=".sync-", dir=cache)
    address = url.format(repo=pin["repo"], rev=pin["rev"])
    request = urllib.request.Request(address, headers={"User-Agent": "turbo-12f-skills"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            files = build(name, pin, response, scratch)
        return scratch, files, tree_sha256(os.path.join(scratch, name))
    except urllib.error.URLError as exc:
        shutil.rmtree(scratch, ignore_errors=True)
        raise SkillError(f"skill {name!r}: cannot fetch {address}: {exc}") from exc
    except BaseException:
        shutil.rmtree(scratch, ignore_errors=True)
        raise


def sync(profile_json, cache, update_hashes=False, url=URL):
    """Sync every pin in the profile's `skills` into `cache`. Returns
    [{name, rev, sha256, files, fetched}]. A pinned hash the fetched tree
    misses raises SkillError; with `update_hashes` the profile file gets the
    computed hashes instead. An entry already cached and verified is not
    fetched again."""
    with open(profile_json, encoding="utf-8") as fh:
        try:
            profile = json.load(fh)
        except ValueError as exc:
            raise SkillError(f"{profile_json} is not valid JSON: {exc}") from exc
    pins = profile.get("skills") if isinstance(profile, dict) else None
    if not isinstance(pins, dict) or not pins:
        raise SkillError(f"{profile_json} has no skills to sync")
    checked = {name: _check_pin(name, pin) for name, pin in pins.items()}
    results, changed = [], False
    for name, pin in checked.items():
        mount = mount_dir(cache, name, pin["rev"])
        files = None
        if pin["sha256"]:
            try:
                verify(cache, name, pin["rev"], pin["sha256"])
                files = [rel for rel, _ in tree_files(os.path.join(mount, name))]
                digest = pin["sha256"]
            except SkillError:
                files = None
        fetched = files is None
        if fetched:
            scratch, files, digest = fetch_into(name, pin, cache, url)
            if pin["sha256"] and digest != pin["sha256"] and not update_hashes:
                shutil.rmtree(scratch, ignore_errors=True)
                raise SkillError(
                    f"skill {name!r}@{pin['rev'][:12]}: the fetched tree hashes to {digest}, "
                    f"pinned {pin['sha256']}; refusing it (--update-hashes to re-pin)")
            if not pin["sha256"] and not update_hashes:
                shutil.rmtree(scratch, ignore_errors=True)
                raise SkillError(f"skill {name!r} has no sha256 pin; the tree hashes to "
                                 f"{digest} (--update-hashes to write it)")
            shutil.rmtree(mount, ignore_errors=True)
            os.chmod(scratch, 0o755)
            os.replace(scratch, mount)
        if digest != pins[name].get("sha256"):
            pins[name]["sha256"] = digest
            changed = True
        results.append({"name": name, "rev": pin["rev"], "sha256": digest,
                        "files": files, "fetched": fetched})
    if changed:
        _write_profile(profile_json, profile)
    return results


def _dump(value, indent=0):
    """JSON, two-space indent; a list or object that fits in 88 columns stays
    on one line, so re-pinning does not reflow the rest of the profile."""
    flat = json.dumps(value, ensure_ascii=False)
    if not isinstance(value, (dict, list)) or not value or len(flat) + 2 * indent <= 88:
        return flat
    pad = "  " * (indent + 1)
    if isinstance(value, dict):
        items = [f"{pad}{json.dumps(k)}: {_dump(v, indent + 1)}" for k, v in value.items()]
        return "{\n" + ",\n".join(items) + "\n" + "  " * indent + "}"
    items = [f"{pad}{_dump(v, indent + 1)}" for v in value]
    return "[\n" + ",\n".join(items) + "\n" + "  " * indent + "]"


def _write_profile(path, profile):
    folder = os.path.dirname(os.path.abspath(path))
    handle, scratch = tempfile.mkstemp(prefix=".profile-", dir=folder)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            fh.write(_dump(profile) + "\n")
        os.chmod(scratch, os.stat(path).st_mode & 0o7777)
        os.replace(scratch, path)
    except BaseException:
        os.unlink(scratch)
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    sync_cmd = commands.add_parser("sync", help="fetch and hash the profile's pinned skills")
    sync_cmd.add_argument("--profile-json", required=True)
    sync_cmd.add_argument("--cache", required=True)
    sync_cmd.add_argument("--update-hashes", action="store_true")
    args = parser.parse_args(argv)
    try:
        results = sync(args.profile_json, args.cache, args.update_hashes, url=URL)
    except (SkillError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    for result in results:
        state = "synced" if result["fetched"] else "cached"
        print(f"{state} {result['name']}@{result['rev']} sha256={result['sha256']}")
        for rel in result["files"]:
            print(f"  {rel}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
