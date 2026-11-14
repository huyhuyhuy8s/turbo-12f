# Contributing to turbo-12f

turbo-12f follows [12-Factor Agents](https://github.com/humanlayer/12-factor-agents)
strictly. Read [`DESIGN.md`](DESIGN.md) for how each factor is built and
[`CONTEXT.md`](CONTEXT.md) for what the factory does not claim before you
change either.

By taking part you agree to the [code of conduct](CODE_OF_CONDUCT.md).
Report security problems privately, as [`SECURITY.md`](SECURITY.md)
describes.

## Rules for the code

- The core (`t12/`) and the tests use only the Python standard library
  and make no network call, hold no credential and call no model.
  Remote work belongs in `adapters/`, which report back through
  `t12 deliver`.
- The model never decides the phase. Control flow lives in
  `t12/model.py`; a worker ends its step with a tool call that
  `t12/tools.py` validates.
- Prefer a check to more prompt text: a gate, a probe, a refusal in the
  core or a stricter field in `t12/tools.py`. A worker can ignore a
  prompt; it cannot ignore a check.
- Prompts in `prompts/` are owned and versioned. A prompt change is a
  reviewed change like any other.
- Replace an API cleanly: migrate every caller and delete the old one.
  Do not leave aliases, shims or re-exports behind.
- A change to the state shape bumps `VERSION` in `t12/model.py`. Older
  runs are then refused and must be superseded.

## Tests

```sh
python3 -m unittest discover -s tests -q
bun test adapters/omp/guard.test.ts
bash scripts/smoke.sh
```

The Python suite takes several minutes. It runs against synthetic git
repos, fake HTTP servers, a fake `docker` and a fake omp
(`tests/fake_omp.py`), never the network or a model. Write tests for
behaviour a user of the factory could see, and keep them deterministic.

A change to how workers behave also needs a live run on a product repo.
`docs/operator-agent.md` explains the setup; record what the run showed
in `DESIGN.md` next to the phase it belongs to.

## Commits and pull requests

Commits use [Conventional Commits](https://www.conventionalcommits.org/):
`feat(runner): ...`, `fix: ...`, `docs: ...`, with `!` and a
`BREAKING CHANGE:` footer for a change that refuses older runs or
removes a command.

`main` is protected: every change lands through a pull request with
one approving review, stale approvals are dismissed by a new push,
review threads must be resolved, and force pushes and deletion are
refused. Merge with a merge commit. `bin/t12-queue` counts a run as
merged only when its candidate commit is in the base branch's history,
and a squash or rebase merge creates new commits.

The pull request template asks what changed, why, and how you verified
it. Update `README.md`, `docs/reference.md` and `DESIGN.md` in the same
pull request when behaviour changes. A human merges; the factory stops
at `local-verified`, including when it works on itself.

## Issues and discussions

Open a bug with the bug report form. A change the factory should
build uses the "Factory task" template and must follow
[`docs/spec-standard.md`](docs/spec-standard.md): a "given X, when Y,
then Z" criterion per behaviour, a `Touches:` line, decisions instead
of open questions, and no HTML comments. Write it with the
`factory-spec` skill and run `spec-check` before filing. On GitHub,
order between issues lives in native sub-issues and "blocked by" links,
never in a `Depends on:` line. A maintainer adds the `factory` label
when the text is approved. Questions go to the Q&A discussions and
design proposals to Ideas.

## Releases

The CLI version is `VERSION` in `t12/cli.py` (`bin/t12 --version`).
It follows [Semantic Versioning](https://semver.org/); before 1.0, a
breaking change bumps the minor number. Record each release in
[`CHANGELOG.md`](CHANGELOG.md).
