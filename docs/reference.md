# turbo-12f reference

What the factory enforces today, section by section. The README has
the overview and quick start; `DESIGN.md` has the reasons; `CONTEXT.md`
has the truth boundaries.

## Sessions and roles

Each role step carries its own session id (`--session` on act and
review); one session serves exactly one role per run, so the red
worker cannot write green's code and the green worker cannot review.
A task lists the reviews `check` requires in `required_reviews`
(default `["reviewer"]`; the one reviewer covers quality and
security).

Roles are names; each maps to one of five kinds, which fix the tools
it may call: `analyzer` (`test_list_ready`), `red` (`red_test_ready`,
`no_cheat_left`), `green` (`green_ready`), `refactor`
(`refactor_done`) and `review` (`review`). Every kind may call
`escalate`. The built-in roles are analyzer, red, green, refactor
and reviewer (`review`). A project adds its own roles in its profile
(see below); `prepare` freezes the run's roles, and `act`, `review`
and `context` refuse a role the run does not have.

## The TDD lane

```text
prepare -> tdd [analyzer -> (red -> green -> refactor)* -> final]
  -> gates -> review -> check -> local-verified
```

The old test-writer / builder lanes and the `red`, `stage`,
`replay` and `green` commands were replaced by the lane above, with
no aliases. The steps are:

- **analyzer** turns the acceptance criteria into a list of items.
  The core refuses a list that leaves a criterion uncovered.
- **red** writes one failing test for the current item. The core
  runs `test_command` on the commit. The test must fail, match
  `assertion_pattern` at the last assertion line the worker named,
  and add at most `red_stub_max_lines` lines outside test files.
  Test files are then locked. When there is no unchecked item, red
  runs a devil pass. A test that adds items (`item_id: "new"`) may
  change no non-test file and must fail against the current
  production code unaided. That keeps it from re-adding a case the
  analyzer pruned as redundant (red sees them as `redundant`) unless
  the real code mishandles it. A devil test that passes means
  `no_cheat_left`.
- **green** makes that test pass with the smallest change, and
  **refactor** changes structure only. Both work in a blind worktree:
  no file matching `test_globs` exists there, they never run tests or
  lint, and green is never shown the acceptance criteria. The core
  applies each patch, then runs every red test created in this run
  plus `lint_command`. A failure goes back to the same role with a
  sanitized error (test paths and names removed); a refactor failure
  rolls the refactor back first. More than `attempt_limit` failures
  pause the run for a human. A green patch skips the refactor step
  when no directives are pending and the patch is at most
  `refactor_skip_max_lines` production lines (added plus deleted,
  summed since the last refactor). The cycle still counts, and
  `tdd-refactor-skipped` is recorded.
- **final** (`t12 tdd-step`, deterministic) runs all red tests and
  lint on the tip commit in the verification sandbox and pins the
  candidate.
- **gates** run the profile's gates one at a time (the existing
  suite, e2e). A gate failure sends the run back to tdd at green, in
  repair mode, with the sanitized gate output.
- **review** is one `reviewer` role covering quality and security.
  `changes-requested` sends the findings to the analyzer (review-fix
  mode); `human-gated` pauses with an escalation.
- **check** (`t12 check`, deterministic) re-hashes all evidence and
  certifies `local-verified`.

**Parallel lanes.** The analyzer may group its items into units,
each owning exact files (`owns`, `tests`, `after`). When two or more
units are accepted and `max_lanes` (default 3; task wins, then the
profile, then a calibrated value in `limits.json`) is above 1, each
unit runs the red-green-refactor loop as its own lane: its own
record (`runs/<task>/lanes/<unit>.json`), lock, branch
(`t12/<task>/<unit>`), worktree and budget share. A lane may write
only its unit's files (`lane-scope` refusal). `t12 tdd-step --unit
U` merges a finished lane into the run branch, one at a time, and
reruns every red test and lint on the merge; a failure goes back to
that lane's green, or to an integration green that may write every
merged unit's files. Lane commands take `--unit U` (`context`, `act`,
`tool-schema`, `tdd-step`, `resume`, `status`), and `board` shows one
row per lane. Overlapping files, globs, cycles in `after` and similar
mistakes are refused at the analyzer step; with one unit the run is
serial, as above. See DESIGN.md "Parallel TDD lanes (Phase 9)".

Gate failures and review rounds each count one repair. When the
repair cap is spent the run pauses with an escalation of kind
`repair-cap`. It does not stop, and a human answers with
`t12 resume --answer`.

## Delivery

The core never touches the network. Adapters do the remote work and
write a report; `t12 deliver` records it:

```bash
export GITHUB_TOKEN=...   # or GITLAB_TOKEN, BITBUCKET_TOKEN, ASANA_TOKEN
python3 adapters/github.py create-pr --home /path/to/state-home \
  --task-id demo-1 --out delivery.json
bin/t12 deliver --home /path/to/state-home --task-id demo-1 \
  --adapter github --report delivery.json
```

Outcomes: `confirmed` (remote read back and matching), `rejected`
(remote said no), `denied` (mutation refused for permission),
`uncertain` (state unknown). An uncertain outcome blocks further
delivery for that adapter until a read-back report is recorded
with `--resolves`. Never retry the action blind. The adapter
never merges; a human merges the PR. See `adapters/README.md`.

Any forge works the same way. `adapters/github.py create-pr`,
`adapters/gitlab.py create-mr` (merge request, `GITLAB_URL` for
self-hosted) and `adapters/bitbucket.py create-pr` (Bitbucket Cloud)
share one contract: find or create the change request for the
branch, read it back, and compare its head to the reviewed
candidate. Record the report with `--adapter github|gitlab|bitbucket`.
The target comes from the task's `github_repo`, `gitlab_project` or
`bitbucket_repo` field, a flag, or the origin remote.

`local-verified` means the configured evidence chain was observed and
is internally consistent. It is not acceptance and not permission to
merge; a human merges.

## Commands

| Command | What it does |
|---|---|
| `init` | Bind a repo + state home, create the directory layout |
| `prepare` | Admit a task: validate repo/base/branch/gates, create worktree + run record (`--supersede` archives a stopped run and starts fresh) |
| `context` | Print the role's prompt and skills + minimal context (Factor 2/3/9); the JSON's `instructions` lists the source, path and SHA-256 of each file used |
| `tdd-step` | The `final` step of the tdd phase: run every red test and the lint command on the tip commit in the sandbox, pin the candidate, move to `gates` (errors unless the next step is `tdd-step`) |
| `gate` | Run the next pending gate in order (`--name N` to name it); failure counts one repair and sends the run back to tdd at green. No role or session: the core runs it |
| `review` | Record a review by any review role of the run: `--role R --session S --verdict approve\|changes-requested --report FILE`; changes-requested sends the findings to the analyzer |
| `check` | Re-hash and re-validate all evidence; requires the final run, passing gates and approving reviews; certify `local-verified` |
| `manifest` | Write the manifest (recorded as an event with its SHA-256) |
| `verify` | Audit the run: event chain, evidence hashes, exit codes, HEAD, manifest |
| `probe` | Run the safety probes; exit 0 only if every probe passes |
| `deliver` | Record an adapter's report as evidence: `--adapter github\|gitlab\|bitbucket\|asana --report FILE [--resolves]` (requires `local-verified`, pinned to the reviewed head) |
| `board` | One row per run, plus one per open lane of a run split into lanes (`task@unit`): phase, candidate, budget left, whether a command is running on it, and who acts next (`--json` for scripts) |
| `report` | Budget report across runs: usage vs budget, cap hits, calibration summary (`--json` for scripts) |
| `retro` | Lessons that recur across runs (gate failures and suite time per gate, rejected worker outputs, review finding classes, repeated escalations), each with a proposed check; changes nothing (`--min N`, `--json`) |
| `calibrate` | Recommend defaults from completed runs; `--apply` writes `limits.json` |
| `status` | Show phase, budgets, gates, candidate |
| `pause` / `resume` | Pause with a reason; resume re-validates from disk (`--answer TEXT` is required while an escalation is open, and records the human's reply) |
| `stop` | Stop a run explicitly with a reason (terminal; use `--supersede` to start over) |
| `escalate` | Record a human question as a tool call and pause (Factor 7) |
| `act` | Take a worker's tool call: `--role R --session S --input FILE [--patch FILE]`. Validates it against `t12/tools.py`, stores it as evidence, and runs the step it maps to: `test_list_ready` (analyzer), `red_test_ready` / `no_cheat_left` (red), `green_ready` and `refactor_done` (with the worker's patch), `review`, `escalate` (Factor 4) |

## Task file

```json
{
  "id": "demo-1",
  "title": "Add a demo behaviour",
  "acceptance_criteria": ["one checkable line per criterion"],
  "base": "main",
  "time_limit_s": 3600,
  "op_limit": 300,
  "repair_cap": 3,
  "test_command": ["python3", "-m", "pytest", "tests/", "-q"],
  "test_globs": ["tests/**"],
  "gates": [
    {"name": "unit", "command": ["python3", "-m", "pytest", "tests/", "-q"]}
  ]
}
```

Gate commands are argv arrays, never shell strings. The task file is
hashed at admission; `check` refuses if the recorded config changed.
Set `"require_probes": true` to hard-gate the tdd steps and gates on
passing safety probes (see `bin/t12 probe`).

Verification commands run sandboxed. By default the adapter is
auto-detected (Landlock on Linux, `sandbox-exec` on macOS, none
otherwise). A task may request `"sandbox_adapter": "docker"` (or the
operator sets `T12_SANDBOX_ADAPTER=docker`): verification then runs
as `docker run --rm --network none` with the worktree and
verify-home mounted and only the verification env passed in. The
image comes from `"sandbox_image"` (default `python:3.12-slim`, or
`T12_SANDBOX_IMAGE`) and must already be present locally:
`prepare` refuses when the Docker daemon does not answer or the
image is missing, so there are no implicit pulls mid-run.

Red, the final test run, and gates run in a fresh checkout of the
commit under test, so untracked and ignored files in the worktree
are invisible to them.
A task that needs gitignored dependency folders (`.venv`,
`node_modules`) lists them in `"verify_deps": [".venv"]`: relative
paths that must stay untracked. Each is linked into the checkout and
is read-only under Landlock and Docker (not under adapter `none`),
and the observation records which deps applied.

Under Landlock only the system dirs (`/usr`, `/opt`, …) are readable.
A toolchain installed under your home (mise, nvm, asdf: node, python)
is not, so `node_modules/.bin/vitest` fails with `Permission denied`.
Set `T12_TOOLCHAIN_DIRS` (a path list of the toolchains' own install
dirs, e.g. `~/.local/share/mise/installs/node/26.13.1`) in the
environment that runs `t12` or the runner: those dirs become readable
and executable, never writable, and each observation lists them.
`/` and the home dir itself are refused. Shebangs that resolve through
a version-manager shim (`#!/usr/bin/env node` with a mise shim first on
`PATH`) can still fail inside the cage; call the toolchain binary by
absolute path in `test_command`, `lint_command` and gates.

Budgets (`time_limit_s`, `op_limit`, `repair_cap`, `cycle_limit`,
`attempt_limit`) are optional in the task file. When omitted,
`prepare` uses `limits.json` in the state home if
`t12 calibrate --apply` has written one, else the built-ins
(3600s / 300 ops / 3 repairs / 50 cycles / 3 attempts). A cycle is
one red-green-refactor round; an attempt is one try at a step before
it is accepted. The run records which source applied
(`budget_source`).

## Project profile

A project keeps what its tasks share in `.t12/project.json`, so a task
file only says what is unique to it:

```json
{
  "version": 1,
  "test_command": ["python3", "-m", "pytest", "-q"],
  "test_globs": ["tests/**", "e2e/**"],
  "lint_command": ["ruff", "check", "."],
  "gates": [{"name": "unit", "command": ["python3", "-m", "pytest", "-q"]}],
  "required_reviews": ["reviewer"],
  "sandbox_adapter": "docker",
  "sandbox_image": "my-product-verify:1",
  "verify_deps": [".venv"],
  "protected_paths": ["guards", "e2e"],
  "security_review_paths": ["src/auth"],
  "roles": {
    "green": {"skills": ["plain-ui-copy"]},
    "ux-copy": {"kind": "review", "prompt": "ux-copy.md"}
  },
  "intake": {"adapter": "github-issues", "repo": "owner/my-product", "label": "factory"}
}
```

`prepare` reads the profile from the trunk branch the state home
names (`t12 init --trunk`, default `main`; a tag of the same name
does not count, and a state home cannot switch trunks). It never
reads it from the task's `base` or the worktree, and it stores the
merged task in the run. A task cannot pick an older commit or
another run's branch to get weaker rules, and a candidate that edits
the profile changes nothing for its own run. Unknown keys are
refused. A leftover `.t12/policy.json` on trunk stops `prepare`
until its `security_review_paths` move into the profile.

A task may tighten the profile but never loosen it, because task files
may come from an issue tracker:

| Key | When both the profile and the task set it |
|---|---|
| `gates` | The profile's gates run first, then the task's |
| `required_reviews` | Both lists apply |
| `require_probes` | On if either turns it on |
| `sandbox_adapter`, `sandbox_image` | Refused unless equal |
| `verify_deps` | The task may only narrow the profile's list; a profile without one allows none |
| `test_command`, `test_globs`, `lint_command`, `assertion_pattern`, `failure_count_pattern`, `red_stub_max_lines`, `refactor_skip_max_lines` | A task may set one only where the profile does not |
| budgets | The task wins |
| `protected_paths`, `security_review_paths`, `roles`, `intake` | Profile only; a task may not set them |
| `max_lanes` | The task wins, then the profile, then the calibrated value |

The tdd keys:

- `test_command` (argv, required from the profile or the task) is
  the command red, green, refactor and `final` all run.
- `test_globs` says which files are tests (default `["tests/**"]`):
  red writes them, they are locked afterwards, and the blind
  worktree of green and refactor has none of them.
- `lint_command` (argv or `null`) runs after green and refactor and
  in `final`. `null` means lint is skipped and the manifest says
  `lint: not configured`.
- `assertion_pattern` (default `AssertionError`) is what a red
  failure must match.
- `failure_count_pattern` (optional, one capture group) must capture
  `1`.
- `red_stub_max_lines` (default 10) caps the production lines red
  may add so a test can compile (a red that adds items, such as the
  devil pass, may add none).
- `refactor_skip_max_lines` (default 5, `0` = never skip) is the
  largest sum of production lines (added plus deleted) that green
  patches may change since the last refactor before refactor runs
  again. A green within the budget skips refactor unless directives
  are pending.

Gates are the existing suite and e2e; the profile's gates run
first.

`tdd-step` refuses a candidate that changes a protected path,
measured from both the task's base and the point where the candidate
leaves trunk, so a base carrying another run's unreviewed edit
cannot hide it. Green and refactor patches that touch a protected
path are refused as they arrive. A move out of a protected path
counts as a change to it. A `verify_deps` entry may not overlap a
protected path or sit in the folder of a protected file, and a dep
reached through a symlink the candidate commits is refused at the
gate. A base with no history in common with trunk is refused at
`prepare`.

Deps still come from the run's worktree, so a worker can put code
in `.venv` or `node_modules` that a gate runs; the profile does not
close that.

`roles` maps a role name to `{"kind", "prompt", "skills"}`. The
built-in roles are always present; a project may add roles and give
any role a prompt or skills, but may not change a built-in's kind.
`required_reviews` must name review roles. With the profile above,
`check` waits for an approving `ux-copy` review as well as the
reviewer's.

`intake` says where tasks come from; only an intake adapter reads it
(see "Taking tasks from an issue tracker"). `prepare` validates it.

`context --role R` reads R's instructions in layers. The prompt is
`.t12/prompts/<prompt>` on the trunk commit when the role names one,
else the factory's prompt of the role's kind: `prompts/tdd-analyzer.md`,
`tdd-red.md`, `tdd-green.md`, `tdd-refactor.md` or `prompts/reviewer.md`
(so `ux-copy`, a project review role, starts from
`prompts/reviewer.md`). Each skill is
`.t12/skills/<name>/SKILL.md` on the trunk commit, else the factory's
`skills/<name>/SKILL.md`; a skill found in neither is refused at
`prepare` and `context`. A worktree edit to `.t12/` changes none of
this for the running task.

## Taking tasks from an issue tracker

A task file need not be hand-written. An intake adapter turns issues
with a label into task files in a queue directory:

```bash
export GITHUB_TOKEN=...
python3 adapters/github-issues.py intake --repo-config path/to/.t12/project.json \
  --out-dir queue --home /path/to/state-home
bin/t12-queue --home /path/to/state-home --queue queue --max 3
```

The issue needs an "Acceptance criteria" heading with a `- ` list;
an issue without one is skipped with the reason printed. The task
file holds only `id` (`gh-<number>`), `title`,
`acceptance_criteria`, optional `touches` and `depends_on`, and
`source: {"adapter", "ref", "url"}`. Gates, reviews, sandbox and
everything else the profile controls never come from an issue.
Re-running intake changes nothing that is unchanged or already has
a run.

An issue whose body contains an HTML comment is skipped: the forge
hides comment text from the maintainer who applies the label, and
intake cannot match every forge's rules for what is hidden.

When an issue loses its label or closes, the next intake run
deletes its queued task file, unless the task already has a run.
GitLab works the same through `adapters/gitlab-issues.py`. The
formats and failure rules are in `adapters/README.md`; the core
never touches the network or holds a token.

Two limits remain. The issue author can edit the body after the
label is applied, and the next intake run picks up the edit: the
label approves the author, not one version of the text. And if
writing a task file fails partway through a run, the queue can hold
some of that run's tasks and not others; run intake again once the
cause is fixed.

`prepare` records `source` in the run record. The GitHub delivery
adapter then puts the source link in the pull request body, with
`Closes #N` when the issue is in the same repo, so merging closes it.
The title and criteria appear in the body inside a code block with
issue references broken, so an issue author cannot close other
issues or hide part of the body.

## Running several tasks at once

Separate runs share one state home and proceed in parallel. A command
that changes a run holds that run's lock, so a second command on the
same run fails with `busy` instead of corrupting its log. Three
exceptions keep parallel lanes from blocking each other: a lane
command (`act`, `tdd-step` or `resume` with `--unit`) holds its
lane's lock, the integration green holds the run's integration lock,
and both take the run lock only around each event append. `status`,
`context` and `tool-schema` read and take no lock. `t12 board` shows
every run, every open lane, and who acts next.

Two optional task fields keep parallel runs from colliding at merge:

- `"touches": ["src/auth", "tests/auth"]` lists the paths the task
  may change. `tdd-step` refuses a candidate that changes anything else
  since the base commit, including the tests red added.
  A task without `touches` may change anything.
- `"depends_on": ["task-a"]` names tasks that must be merged first.

`bin/t12-queue --home <state> --queue <dir> --max N` makes one pass
over a directory of task files in file-name order. It prepares each
task whose dependencies are local-verified and merged into their
base, whose `touches` overlap no unmerged run, and that fits under N
active runs. Every other task stays queued with its reason. A task
without `touches` overlaps everything, so it runs alone. Start one
orchestrator per `started` line and run the pass again whenever a
run finishes or a merge lands; a task that already has a run is never
prepared twice.

The queue counts a run as merged when its candidate commit is in the
base branch's history, as after a merge commit or a fast-forward. A
squash or rebase merge creates new commits, so the queue never sees
that run as merged: tasks that depend on it, or overlap it, wait. Use
merge commits for factory branches.

Runs prepared before the TDD lane (state version 1) are refused by
every command; supersede them. Runs prepared before `touches` or
project profiles existed are refused the same way.

## Improving the factory

`t12 retro` reads every run's event log and structured reviews and
lists what keeps recurring: a gate that fails in most runs, a worker
output rejected for the same reason, a review finding class, a
question escalated again and again. Each comes with a proposed check.
Prefer a check (a gate, a probe, a refusal in `tdd-step`, a stricter
field in `tools.py`) over more prompt text: a worker can ignore a
prompt, not a check. Like `calibrate`, `retro` only proposes. Turn a
proposal into a task file and run it through the factory; a human
merges.

## Letting the factory work on itself

turbo-12f is a repo like any other, so a `retro` proposal can become
a task aimed at turbo-12f. Two rules keep that safe:

- **The candidate never checks itself.** Run the factory from an
  installed copy of `main` (or the `turbo-12f` image through
  `bin/t12-container`), never from a worktree. `t12` refuses to run
  when its own code sits inside the state home it is given.
- **Profile changes need a security review.** A product repo may list
  paths in `.t12/project.json` as `"security_review_paths"`.
  `tdd-step` reads the profile from the trunk commit, not the
  candidate, and tells the reviewer when the candidate changes a
  listed path or anything under `.t12/` (`security_paths_touched` in
  the review context). The one reviewer covers security; there is no
  separate security role. turbo-12f lists its state
  machine, profile, sandbox, evidence and storage code, and its prompts.

The factory still stops at `local-verified`. A human merges.

## Running in a container

`bin/t12-container` takes the same arguments as `bin/t12` and runs
the whole factory inside `docker run`:

```sh
docker build -t turbo-12f container/
bin/t12-container init --repo /path/to/product --home /path/to/state-home
bin/t12-container prepare --home /path/to/state-home --task examples/task.json
```

The container runs as your uid and gid with no network, a read-only
root filesystem, and no capabilities. It can see only these paths:

- this checkout, read-only
- the current directory
- every path argument (a file argument mounts its parent directory)
- the product repo recorded in the state home

Each path is mounted at its host location, so a state home works
from the host and from the container in either order. Your
`git config user.name` and `user.email` are passed in as
`GIT_AUTHOR_*` and `GIT_COMMITTER_*`. Nothing else from your
environment goes in.

The image comes from `T12_IMAGE` (default `turbo-12f`, built from
`container/Dockerfile` with `python3`, `git`, and `pytest`). It must
contain every tool your gate commands call, so add yours to the
Dockerfile or point `T12_IMAGE` at your own image. The image must
already be present locally. The launcher does not pull or build it.

Verification inside the container uses Landlock, as it does on
the host. The `docker` sandbox adapter is not available inside the
container, and a task that requests it is refused at `prepare`.
Reaching Docker from inside would mean mounting the Docker socket,
which gives the container root on the host.

## Caging a worker command (optional, not part of the omp flow)

The built-in flow does not use this. omp workers run under the guard
extension and, for green and refactor, in a blind worktree, and the
core commits every role's change itself. `bin/t12-cage` stays as
optional hardening for a worker you run by hand outside that flow,
and is flagged as removable.

`bin/t12-cage` runs a worker's command in a Landlock cage, so the
worker cannot tamper with what the factory later trusts:

```sh
bin/t12-cage --home /path/to/state-home --task-id demo-1 -- <worker command...>
bin/t12-cage commit --home /path/to/state-home --task-id demo-1 \
  --message "feat: implement demo"
```

Inside the cage the command starts in the run's worktree and can
write only the worktree and a private temp dir (`TMPDIR`, removed on
exit). It can read the system directories and the shared `.git`, so
`git status`, `git diff`, and `git log` work. It cannot write the
shared `.git` (hooks, config, `refs/replace`, `info/attributes`) or
the state home, and it cannot open TCP connections. UDP stays open,
so the cage does not stop data from leaving. Git inside the cage
reads no global config, and the `XDG_*_HOME` directories point into
the private temp dir, because your own are not readable there.

The command runs in its own PID namespace. No process it started
outlives `t12-cage`, including one that detached with a double fork
and `setsid`: the kernel kills them all when `t12-cage` returns, when
`SIGINT`, `SIGTERM`, or `SIGHUP` stops it, and when it is killed with
`SIGKILL`. After a `SIGKILL` they die a moment later, and the private
temp dir stays behind. The command cannot signal or limit a process
outside the namespace. Tools that read `/proc`, such as `ps`, still
list the host's processes there, under pids that mean nothing inside
the cage.

The cage needs Linux with Landlock ABI 5 or later (kernel 6.10) and
unprivileged user namespaces, which a PID namespace requires. Some
hosts turn those off (`kernel.unprivileged_userns_clone=0`,
`user.max_user_namespaces=0`, Ubuntu's
`kernel.apparmor_restrict_unprivileged_userns=1`, Docker's default
seccomp profile). When either is missing, `t12-cage` exits non-zero
and does not run the command.

The user namespace maps only your own uid and gid, so files owned by
anyone else show as `nobody`. Inside another Landlock cage that makes
`/proc` read-only, as in this repo's own gates, `t12-cage` cannot
write that map. It says so on stderr, and the command then sees uid
65534 for itself and for every file. File access is the same either
way.

| Option | Effect |
|---|---|
| `--read PATH` | Also let the command read PATH (repeatable). An agent CLI installed under your home directory needs this. |
| `--write DIR` | Also let the command write DIR (repeatable). Refused when DIR overlaps the shared `.git` or the state home. |
| `--network` | Allow TCP, for a worker that calls a model. UDP is open either way. |

A linked worktree keeps its index and refs in the shared `.git`, so
a caged worker cannot commit. Run `t12-cage commit` yourself,
outside the cage, after `t12-cage` ends. It commits every change in
the worktree on the run's branch. It refuses when the worktree's
`.git` pointer file is not the one `prepare` wrote, or when the
worktree contains a nested git repository. It passes git the git dir
`prepare` created and runs no hooks. When it refuses, stop the run:
`t12` commands do not make these checks themselves. CONTEXT.md lists
what the cage leaves open.
