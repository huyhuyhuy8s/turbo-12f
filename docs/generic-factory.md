# Generic factory: one factory, many projects

Status: steps 1 (project profile), 2 (roles and skills), 4 (intake)
and 6 (the TDD lane) are built;
the rest is a proposal.
`docs/reference.md` describes what is enforced today.

## Goal

turbo-12f should deliver any project, not only chat-bridge. A
project tells the factory how it works through one config file. The
factory keeps the evidence chain (tdd, gates, review, check)
the same for every project.

## What is project-specific today

The core has no chat-bridge strings. Before step 2, five
things were fixed in code and only changed by editing the factory.
Two still are:

| Concern | Where it is fixed today |
|---|---|
| Task intake | hand-written `task.json` only; every task repeats gates, sandbox, `verify_deps` |
| Delivery | `ADAPTERS` in `t12/delivery.py`; GitHub, GitLab, Bitbucket and Asana scripts |

chat-bridge works around this with its own in-repo factory
(`chat-bridge/factory/`), which hardcodes `PROTECTED_PATHS`, its
gate ladder, and its lanes. Before the project profile, turbo-12f had
no protected-path check at all, so it could not enforce the rule that `guards/`, `e2e/`,
`bench/`, and `docs/health/` are read-only to every worker.

## Data shape

### Project profile

The profile is `.t12/project.json` in the product repo.

```json
{
  "version": 1,
  "name": "chat-bridge",
  "base": "main",
  "test_command": ["python3", "-m", "pytest", "tests/", "-q"],
  "test_globs": ["tests/**", "e2e/**"],
  "lint_command": ["ruff", "check", "."],
  "gates": [
    {"name": "unit", "command": ["python3", "-m", "pytest", "tests/unit", "-q"]},
    {"name": "integration", "command": ["python3", "-m", "pytest", "tests/integration", "-q"]}
  ],
  "verify_deps": [".venv"],
  "sandbox": {"adapter": "docker", "image": "chat-bridge-verify:1"},
  "protected_paths": ["guards/", "e2e/", "bench/", "docs/health/", "AGENTS.md", ".t12/"],
  "context_docs": ["AGENTS.md", "docs/spec.md", "docs/architecture.md"],
  "roles": {
    "green": {"skills": ["plain-ui-copy"]},
    "reviewer": {"kind": "review"},
    "ux-copy": {"kind": "review", "prompt": "ux-copy.md"}
  },
  "required_reviews": ["reviewer", "ux-copy"],
  "skills": {
    "vitest": {"repo": "antfu/skills", "rev": "d524ed287eef22757297827ce42e3cad88abd20c",
               "path": "skills/vitest", "include": ["SKILL.md", "references/**"],
               "roles": ["red"], "when": {"dependency": "vitest"},
               "sha256": "5f8d6143d0409bbf781f2a9b97b1aeaa8422b7ee3a518d4c767c8dbfbbeae915"},
    "ponytail": {"repo": "DietrichGebert/ponytail", "rev": "96608e078b2d5ea607c5c3b8b5a54d1c1fba6846",
                 "path": "skills/ponytail", "include": ["SKILL.md"],
                 "roles": ["green", "refactor"], "when": null,
                 "sha256": "8c528dc79e2839e56beac2fefe7d7bdaac7ce165f46c9db1b1662ddd9667df46"},
    "impeccable": {"repo": "pbakaus/impeccable", "rev": "1995764b4936f2bfbf4eb719cb37b384a45ceacb",
                   "path": ".agents/skills/impeccable", "include": ["SKILL.md", "reference/**"],
                   "roles": ["green"],
                   "when": {"files": ["**/*.tsx", "**/*.jsx", "**/*.vue", "**/*.svelte", "**/*.css", "**/*.html"]},
                   "sha256": "1816625401fcb2a871409648e05dc9042d00025de2212509f3cc446b8e84e92f"},
    "humanizer": {"repo": "blader/humanizer", "rev": "b48c832f0f809c1b5658edbb838c2521b72f2413",
                  "path": ".", "include": ["SKILL.md"], "roles": ["refactor"], "when": null,
                  "sha256": "fa244ff2f8570c519add7dfd9a7594a54d48908890835b21e870b5b20aa04867"},
    "safe-refactor": {"repo": "juliusbrussee/caveman", "rev": "b4ee71d557d96e3cbc14f88fa6e953a1362865f5",
                      "path": "skills/safe-refactor", "include": ["SKILL.md"],
                      "roles": ["refactor"], "when": null,
                      "sha256": "e79f47cdc8897ee295d6108975ec6cb1201f92bf1e8253c44d89e8681317e876"},
    "security-review-anthropic": {"repo": "anthropics/claude-code-security-review",
                                  "rev": "0c6a49f1fa56a1d472575da86a94dbc1edb78eda",
                                  "path": ".claude/commands/security-review.md", "wrap": true,
                                  "roles": ["reviewer"], "when": null,
                                  "sha256": "17161bfce7efc5139fa44f2f662043140cd59c1f3eecddf40b9dc2fb29dd9526"},
    "security-review": {"repo": "getsentry/skills", "rev": "e94f22214528d69899d09c627b3315ddc3544816",
                        "path": "skills/security-review",
                        "include": ["SKILL.md", "SPEC.md", "LICENSE", "languages/**", "infrastructure/**"],
                        "roles": ["reviewer"], "when": null,
                        "sha256": "c52cfa3ccce699901c4179d36683f3904da6b8ae3d3a1cbb909b73a42805d9ea"}
  },
  "intake": {"adapter": "github-issues", "repo": "owner/chat-bridge", "label": "factory"},
  "delivery": [{"adapter": "github", "repo": "owner/chat-bridge"}]
}
```

Rules that follow from this shape:

- **The profile is read from trunk, not the task's base or the worktree.**
  `prepare` reads `.t12/project.json` at the trunk commit the state
  home names, validates it, and freezes the merged settings and the
  trunk commit in the run record. A task cannot choose an older or
  unmerged commit to get weaker rules, and a worker that edits
  `.t12/` changes nothing for its own run.
- **A task file only holds what is unique to the task.** `id`,
  `title`, `acceptance_criteria`, and an optional `source` ref. Any
  profile key a task sets overrides the profile for that run, and
  the override is recorded.
- **`protected_paths` becomes a tdd check.** `tdd-step` refuses a
  candidate whose diff from base touches a protected path, and the
  core refuses a green or refactor patch that does. `.t12/`
  is always protected, so a run cannot rewrite its own rules.
- **No profile means the built-in defaults.** Roles analyzer, red,
  green, refactor and reviewer; `required_reviews: ["reviewer"]`; no
  intake; delivery through the command line. `test_command` is
  still required, from the task file.
- **`skills` pins third-party skills per role, by commit.** Each pin
  names a GitHub `repo`, a 40-hex `rev`, a `path` inside it, the
  `include` globs to keep (or `wrap: true` for one command file that
  becomes a `SKILL.md`), the profile `roles` that get it, a `when`
  (`null`: always; `{"dependency": "vitest"}`: the repo's root
  `package.json` lists it; `{"files": [globs]}`: the base commit tracks
  a matching file), and the `sha256` of the synced tree. The core
  checks shape only, and `t12 context` lists the pins a role gets on
  this run as `skills: [{name, rev, sha256}]`, with `when` read from
  the base commit by git alone. `adapters/skills.py sync` fetches and
  hashes them; the runner verifies the hash, mounts exactly those
  skills in the omp session, and records them in the transcript (see
  `adapters/README.md`). The example pins `red` the vitest skill only
  where the product uses Vitest, `green` ponytail (and impeccable for
  UI files), `refactor` ponytail, humanizer and safe-refactor, and
  `reviewer` both security-review skills.

### Roles are named; kinds are fixed

A role is a name the project picks. A kind is what the core knows
how to enforce. There are five kinds:

| Kind | Tools it may call | What the core does with it |
|---|---|---|
| `analyzer` | `test_list_ready` | records the item list; refuses one that leaves an acceptance criterion uncovered |
| `red` | `red_test_ready`, `no_cheat_left` | verifies the failing test, commits and locks it |
| `green` | `green_ready` | applies the patch from the blind worktree, runs the red tests and lint |
| `refactor` | `refactor_done` | the same, and rolls the patch back on failure |
| `review` | `review` | records a verdict; `required_reviews` names which review roles `check` needs |

All kinds may call `escalate`. `TOOLS` is keyed by kind. `prepare`
merges the built-in roles (analyzer, red, green, refactor, and
reviewer as `review`) with the profile's `roles`, and freezes the
result in the run record as `role_map`. `act`, `review`, `context`,
session binding, and `required_reviews` all check roles against that
map. A project may add roles and set a prompt or skills on a
built-in, but may not change a built-in's kind. A task file may not
set `roles`. The one-session-per-role rule is unchanged. A
project adds a reviewer (UX copy, accessibility, data privacy) by
adding a `review` role with its own prompt. It needs no change to
the core. Security is not a separate role: the one `reviewer`
covers quality and security.

### Prompts and skills resolve in layers

`t12 context --role R` builds the prompt from:

1. The role's prompt: `.t12/prompts/<file>` at the trunk commit if
   the profile names one, else the factory's prompt of the role's
   kind (`prompts/tdd-analyzer.md`, `tdd-red.md`, `tdd-green.md`,
   `tdd-refactor.md`, `reviewer.md`); a project role of kind `review`
   starts from `prompts/reviewer.md`, so a `ux-copy` role needs
   only its own prompt to differ.
2. Each skill the role lists: `.t12/skills/<name>/SKILL.md` at the
   trunk commit, else the factory's `skills/<name>/SKILL.md`. A skill
   found in neither place is an error, at `prepare` and at `context`.
3. `context_docs`, as paths the worker must read. They are listed,
   not inlined, so the context stays small (Factor 3). Not built yet.

The context JSON's `instructions` list records the source (`project`
or `factory`), path, and SHA-256 of every prompt and skill it used,
so a review can tell which instructions a worker saw.

### Intake and delivery are adapters with a fixed contract

Adapters stay separate programs that hold credentials. The core
never imports one (this is unchanged).

- **Intake** is an adapter run as `adapters/<name>.py intake
  --repo-config <profile> --out-dir <queue dir>`. It writes task
  files in the task schema, each with
  `source: {"adapter": "github-issues", "ref": "o/r#12", "url": "..."}`,
  and never a profile-controlled key. `bin/t12-queue` admits them as
  it admits a hand-written task. Built: `github-issues`,
  `gitlab-issues`. Planned: `file`, `jira`, `linear`, `asana`.
- **Delivery** keeps today's report schema and four outcomes. The
  `ADAPTERS` tuple is replaced by the profile's `delivery` list.
  `deliver` refuses an adapter the frozen profile does not name.
  Planned change-request adapters are `github` (exists), `gitlab`
  (merge request), and `bitbucket` (pull request). They share one
  contract: read the branch, find or create the change request,
  read it back with the reviewed head, and never merge. The
  change-request body links `source.url`, so the issue closes
  through the forge's own syntax.
- **Adapters come from the factory or the state home, never the
  product repo.** An adapter runs on the host with tokens. Loading
  one from the repo the agents write to would let a worker run code
  with those tokens. A project that needs a custom adapter puts it
  in `<state-home>/adapters/` and names it in the profile.

## Workflow: as configurable as the invariants allow

Rule: a project configures the data the phase machine reads. It never
configures the machine. Every item below is data; every fixed item
names the factor or fix it protects.

A project can set:

- the gate ladder: which checks run (the existing suite, e2e), their
  commands, their order
- the tdd settings: `test_command`, `test_globs`, `lint_command`,
  `assertion_pattern`, `failure_count_pattern`, `red_stub_max_lines`,
  `refactor_skip_max_lines`
- its own roles, each mapped to a kind, with any number of review
  roles and the set `check` requires
- prompts and skills per role, from `.t12/` before the factory's
- pause points where a human must resume, such as before `check` or
  before delivery (Factor 6 and 7)
- budgets (including `cycle_limit` and `attempt_limit`), sandbox,
  `verify_deps`, protected and security-review paths, `touches` and
  `depends_on`
- intake and delivery adapters
- how often a pattern must recur before `retro` reports it

A project cannot change:

- the phase order: prepare, tdd (analyzer, red, green, refactor,
  final), gates, review, check. The state machine is the only
  control flow (Factor 8).
- one JSON tool call per worker, with a fixed tool set per kind, so
  every tool maps to a step the core checks (Factor 4)
- one session per role (Factor 10)
- that green and refactor work in a blind worktree with no test
  file, and green never sees the acceptance criteria
- that the profile comes from trunk and is frozen at
  `prepare`, and that a change to `.t12/` is flagged to the reviewer.
  This is "the candidate never checks itself" for project settings.
- that `touches` is enforced at `tdd-step`; without it the queue's
  no-overlap promise is a claim
- that the core never calls a model or the network and never merges;
  `retro` and `calibrate` propose, a human applies

## Quality control: core, profile, or outside

Quality control here is everything after the tdd lane: gates, review,
check, and `local-verified`. Red belongs to tdd; its tests are the
build's own oracle, not an independent check.

One rule sorts every QC idea. The **core** gets a mechanism only when
every project needs it and it stays deterministic, offline, stdlib only
and free of any one language. The **profile** holds the commands and
data a mechanism reads, including work that is not about one task: a
whole-product job runs outside the per-task pipeline, but the profile
declares it, and what it finds reaches the factory as an issue.

### Two full suites

The factory's own suite (this repo's `tests/`) and a product's suite
are different things, and each runs only for its own repo:

| | Factory suite | Product suite |
|---|---|---|
| Declared in | this repo's `.t12/project.json` | the product's `.t12/project.json` |
| Runs when | a task changes the factory itself | a task on that product |
| Never runs | during a product run | during a factory run |

A run on a product is driven by an installed copy of the factory's
`main` (the candidate never checks itself), so a product run never pays
for the factory's suite. Inside one repo, `test_command` is what each
tdd cycle runs and should stay narrow; the full suite belongs in a
gate. A gate's `when` (below) lets a slow gate run only when the
candidate's diff touches the paths it covers.

### Where each idea lives

| Idea | Lives in | Why |
|---|---|---|
| Gate `when`: run a gate only when the diff from base touches its globs; a skipped gate is recorded with its reason | core mechanism, profile data | the profile configures data, not flow (Factor 8) |
| Review records pass or fail per acceptance criterion; `merge-ready` is refused while any criterion is not `pass` | core | a refusal in `tools.py` and `act` (Factor 4) |
| Known red: a failed gate runs again on the base commit; failing there too pauses the run instead of spending a repair | core | green cannot fix what trunk already breaks (Factor 9) |
| A failed gate reruns once on the same commit; a pass on the rerun is recorded as flaky | core | decided from recorded results (Factor 12) |
| Advisory gate: its output goes to the reviewer's context and never sends the run back | core | the slot for language-specific evidence (Factor 3) |
| Holdout acceptance author with the base commit as its kill check | core mechanism, opt-in by profile | a small focused role (Factor 10); the project supplies the command |
| Route back to red when a red test is wrong | core | a state machine transition (Factor 8); open, see #88 |
| Mutants and coverage of the diff | profile, as advisory gates | the core cannot mutate every language |
| Flake quarantine per test | profile (the test runner) | a gate is one command; the core sees no single tests |
| Smoke, contract, static analysis, integration or load against local stand-ins | profile gates | data only |
| Property-based tests | profile skill pin with `when: {"dependency": ...}` | prompts in layers (Factor 2) |
| UI and usability review | profile review role and image | a project role, no core change |
| Formal verification (TLC, Dafny, Kani, CBMC) | profile gate with `when` | any project; one command that exits 0 or not |
| Fuzz, penetration, performance, whole-repo scans | profile `jobs`, run by `bin/t12-job` | slow and about the whole product; cron, CI or a person starts them (Factor 11) |
| Production signals (Sentry, SonarQube, cubic) | profile `jobs` of the adapter kind | a host adapter with the credentials; findings become issues |

### Whole-product jobs

A project lists its jobs in the profile's `jobs` key. A `command` job
runs in the cage on the trunk tip, offline, like a gate. An `adapter`
job runs a host program from the state home or the factory (never the
product repo) that holds the credentials and may use the network.
`bin/t12-job --home HOME --name NAME` runs one job and writes a
findings file; the core schedules nothing. `adapters/job-findings.py`
files a run's new findings as one issue, deduplicated by fingerprint,
and never adds the approval label: a finding has no acceptance
criteria, so a person writes the spec and approves it (Factor 7).
Tracked in #115.

None of this moves the human merge. `local-verified` gains evidence but
still means "the evidence chain was observed", not acceptance. The core
items are tracked in #107; every new key defaults to today's behaviour,
because the factory cannot edit the existing tests a changed default
would break.

## Plan

Each step is one PR on top of the last. Each ends in a check that
runs against a disposable repo, as the current tests do.

1. **Project profile. Built.** `.t12/project.json` replaces
   `.t12/policy.json`. Read from the configured trunk, merged under
   tighten-only rules, protected paths refused at `tdd-step`.
2. **Roles and skills. Built.** Role-to-kind map, `TOOLS` keyed by kind,
   layered prompt and skill resolution, hashes in the context. The
   check is a project-defined `ux-copy` review role that blocks
   green until it approves.
3. **Delivery adapters.** The profile's delivery list replaces
   `ADAPTERS`. GitLab and Bitbucket adapters, tested against fake
   HTTP servers with the existing outcome rules (a failed read is
   uncertain, never denied).
4. **Intake adapters. Built.** Not `t12 intake`: intake adapters are
   programs (`adapters/github-issues.py`, `adapters/gitlab-issues.py`)
   that write task files into the queue directory, so the core stays
   off the network. The profile's `intake` key sets their defaults,
   and `source` goes from the issue into the run record and the
   change-request body. The GitHub delivery adapter links it; the
   GitLab and Bitbucket ones link it when step 3 lands.
5. **Onboard chat-bridge.** Write its `.t12/project.json` from
   `chat-bridge/factory/paths.py` and its gate ladder. Run one real
   issue end to end. Then retire `chat-bridge/factory/` in a
   chat-bridge PR.
6. **TDD lane. Built.** `prepare -> tdd -> gates -> review ->
   check` replaced the red/stage/replay/green lanes (history), with
   five built-in roles, a blind worktree for green and refactor, and
   omp as the worker. It is checked against disposable repos, as the
   other steps are; see DESIGN.md "TDD lane (Phase 8)".
7. **QC core.** The core mechanisms in "Quality control: core,
   profile, or outside", each off by default and turned on by the
   profile: gate `when`, advisory gates, gate reruns, the base check,
   and per-criterion review verdicts (#107 and its sub-issues). Step 5
   comes first: the factory has so far delivered only itself.

## Open questions

- **Gate ladders per task type.** chat-bridge has QC-owned gates 4
  to 6 that run in CI, not locally. The proposal records them as
  out of scope for `local-verified`. A project could instead declare
  `ci_gates` that delivery reads back from the forge.
- **Model invocation.** The core never calls a model. The runner
  (`adapters/runner.py`) drives omp workers and lives
  in `adapters/`, not the core. Whether it should also learn other
  worker backends is open; omp is the only worker today.
