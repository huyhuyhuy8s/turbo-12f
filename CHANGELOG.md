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
- Delivery from the runner: a profile `delivery` key
  (`{"adapter": "github" | "gitlab" | "bitbucket"}`) makes the runner
  push a `local-verified` run, open the change request and record it
  with `t12 deliver`. A failed step prints the command that retries it,
  and an `uncertain` delivery is never repeated.
- Asana tasks intake (`adapters/asana-tasks.py`): tagged tasks become
  task files, sub-tasks run on their own and a task with open sub-tasks
  never runs.
- Intake enforces `docs/spec-standard.md` (`spec_check` in
  `adapters/common.py`): at most 10 criteria, a `Touches:` line, no open
  questions, no parent issues. `python3 adapters/common.py spec-check`
  runs the same rules on a draft. GitHub intake reads native "blocked
  by" relations.
- `bin/t12-wizard`: `setup`, `take`, `status`, `run`, `answer`,
  `deliver` and `sync` for a workspace of a factory clone and a product
  clone. `run` starts the omp runner and prints a progress line.
- A worker's output for a superseded run is refused: the run record
  carries a `run_id`, the runner passes it to `t12 act --run-id`, and
  the refusal ends the omp session and is logged on the archived run.
- Time spent in `paused` no longer counts against the time budget.
- A `final` refused because a changes-requested review was answered with
  no new commit pauses the run, with the review report in the question.
- A red refused for an error that is not an assertion failure says to
  add a stub when the code under test does not exist yet.

### Left out on purpose

- `step_retries` (retry a rejected step with a hint, then escalate). The
  omp runner returns a refused call to the same session and the core's
  `attempt_limit` escalates, so nothing would enforce the key.
- Accepting a setUp that loads code not written yet as a red. A red
  must fail at its named last assertion; a stub makes missing code fail
  there.
- The agy runner schema flags; agy is not a worker.

### Docs

- README rewritten as an overview; the full reference moved to
  `docs/reference.md`.
- New `CONTRIBUTING.md`, `SECURITY.md` and `CODE_OF_CONDUCT.md`.

## 0.1.0

The first version: the deterministic core, safe execution, the
hash-chained evidence log, budgets and calibration, separate role
sessions, delivery adapters and issue intake (DESIGN.md roadmap phases
1 to 7).
