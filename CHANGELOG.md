# Changelog

Releases of [huyle-gradion/turbo-12f](https://github.com/huyle-gradion/turbo-12f).
The CLI version is `VERSION` in `t12/cli.py`. Versions follow
[Semantic Versioning](https://semver.org/); before 1.0 a breaking change
bumps the minor number. Questions go to huy.leminh@gradion.com, security
reports as [`SECURITY.md`](SECURITY.md) describes.

## 0.2.0 (2026-11-15)

### Breaking

- The test-writer, builder, QA and security roles are gone, and so are
  the `red`, `stage`, `replay` and `green` commands. One TDD lane
  replaces them: analyzer, then red, green and refactor per item, then
  `final`, gates, one reviewer and `check` (the old `green` command).
- Run records are state version 3. Every command refuses a run prepared
  by an older version; supersede it.
- The runner drives omp workers only and has no `--model-cmd`.

### Added

- The TDD lane (DESIGN.md "TDD lane (Phase 8)"). Green and refactor
  work in a blind worktree with no test files, and the core runs the
  red tests and `lint_command` after each of their patches.
- Parallel TDD lanes (DESIGN.md "Parallel TDD lanes (Phase 9)"): the
  analyzer may split the list into units with disjoint files, each runs
  as its own lane, and `t12 tdd-step --unit` merges them one at a time.
  New task and profile key `max_lanes`.
- `refactor_skip_max_lines`: refactor is skipped after small greens.
- Runner flags `--role-model`, `--role-thinking`, `--lane-cap`,
  `--backoff-base` and `--backoff-max`. A rate-limited omp session uses
  no attempt and is retried after a backoff.
- Pinned third-party skills per role (`adapters/skills.py sync`).
- `T12_TOOLCHAIN_DIRS` makes host toolchains readable inside the
  Landlock cage.
- FactoryBench attempt reporting (`adapters/fb_report.py`).

### Docs

- README rewritten as an overview; the full reference moved to
  `docs/reference.md`.
- New `CONTRIBUTING.md`, `SECURITY.md` and `CODE_OF_CONDUCT.md`.

## 0.1.0

The first version: the deterministic core, safe execution, the
hash-chained evidence log, budgets and calibration, separate role
sessions, delivery adapters and issue intake (DESIGN.md roadmap phases
1 to 7).
