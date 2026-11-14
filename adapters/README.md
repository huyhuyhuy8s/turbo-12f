# Adapters

Adapters are separate programs that run outside the factory core.
They hold the host's credentials and talk to the network. The core
never imports these scripts.

- **Intake** adapters turn issues into task files (see "Intake
  adapters" below).
- **Delivery** adapters write a JSON report that `t12 deliver`
  records as evidence (the rest of this file).

## GitHub — create a pull request (never merge)

```bash
export GITHUB_TOKEN=...
python3 adapters/github.py create-pr --home <state-home> --task-id <id> \
  --out delivery.json [--repo owner/name] [--title T] [--body B]
bin/t12 deliver --home <state-home> --task-id <id> \
  --adapter github --report delivery.json
```

- The branch must already be pushed; the adapter only reads and
  creates the PR, then reads it back.
- The repo comes from `--repo`, the task's `github_repo` field,
  or the origin remote.
- If a PR already exists for the branch with the reviewed head,
  the outcome is `confirmed` and no duplicate is created. If its
  head is a different commit, `rejected`.
- When the run has a `source` (the task came from intake), the
  default body ends its summary with `Closes #N` if the source is an
  issue of the same repo as the PR, and always with a `Source:` link
  to the issue. An issue in another repo or tracker gets only the
  link, so merging never closes it. The task's title, acceptance
  criteria and manifest follow inside code blocks, with a WORD JOINER
  after each `#` that precedes a digit: an issue author wrote that
  text, so it can neither close an issue nor hide the rest of the
  body. The default PR title breaks issue references the same way.
  An explicit `--body` is used as given.

## GitLab — create a merge request (never merge)

```bash
export GITLAB_TOKEN=...
export GITLAB_URL=https://gitlab.example.com   # default https://gitlab.com
python3 adapters/gitlab.py create-mr --home <state-home> --task-id <id> \
  --out delivery.json [--project group/name] [--title T] [--body B]
bin/t12 deliver --home <state-home> --task-id <id> \
  --adapter gitlab --report delivery.json
```

- Uses the REST v4 API at `$GITLAB_URL/api/v4`. The token is sent
  as a bearer token.
- The project comes from `--project`, the task's `gitlab_project`
  field, or the origin remote (only when its host is the
  `GITLAB_URL` host). Nested groups work: `group/sub/name`.
- The branch must already be pushed. The adapter finds an open MR
  for the branch, creates one if none, then reads it back and
  compares its `sha` to the reviewed head. An existing MR with the
  reviewed head is `confirmed` with no duplicate; with another
  head, `rejected`.

## Bitbucket Cloud — create a pull request (never merge)

```bash
export BITBUCKET_TOKEN=...
export BITBUCKET_URL=https://api.bitbucket.org/2.0   # the default
python3 adapters/bitbucket.py create-pr --home <state-home> --task-id <id> \
  --out delivery.json [--repo workspace/slug] [--title T] [--body B]
bin/t12 deliver --home <state-home> --task-id <id> \
  --adapter bitbucket --report delivery.json
```

- The token is sent as a bearer token.
- The repo comes from `--repo`, the task's `bitbucket_repo` field,
  or the origin remote (only on `bitbucket.org`).
- Same flow as GitLab: find an open PR for the branch, create one
  if none, read back `source.commit.hash`. Bitbucket may return an
  abbreviated hash, so it is compared as a prefix of the full
  reviewed SHA. A hash shorter than 12 characters cannot be
  compared, so the outcome is `uncertain`, never `confirmed`.

## Asana — mark a task complete

```bash
export ASANA_TOKEN=...
python3 adapters/asana.py complete --home <state-home> --task-id <id> \
  --out delivery.json [--task <asana-gid>]
bin/t12 deliver --home <state-home> --task-id <id> \
  --adapter asana --report delivery.json
```

- The Asana task GID comes from `--task` or the task's
  `asana_task_gid` field.
- The adapter comments with the manifest summary + candidate SHA,
  marks the task complete, and reads it back. An already-complete
  task is `confirmed` with no duplicate comment.

## Intake adapters

```bash
export GITHUB_TOKEN=...          # or GITLAB_TOKEN and GITLAB_URL
python3 adapters/github-issues.py intake --out-dir <queue dir> \
  [--repo-config repo/.t12/project.json | --trunk-repo repo --trunk main] \
  [--repo owner/name] \
  [--label factory] [--home <state-home>] [--api-base https://api.github.com]
python3 adapters/gitlab-issues.py intake --out-dir <queue dir> \
  [--repo-config ...] [--repo group/project] [--label factory] [--home ...]
export ASANA_TOKEN=...
python3 adapters/asana-tasks.py intake --out-dir <queue dir> \
  [--repo-config ...] [--project GID] [--tag NAME] [--home ...]
bin/t12-queue --home <state-home> --queue <queue dir> --max 3
```

Each writes one `<id>.json` per usable issue into the directory
`bin/t12-queue` reads, then prints one line per issue:
`wrote`, `unchanged`, `skipped` (with the reason), or `has-run`, and
one `withdrawn` line per task file it deletes.
`github-issues` makes ids `gh-<number>`, `gitlab-issues` `gl-<iid>`,
`asana-tasks` `as-<gid>`.
`GITLAB_URL` defaults to `https://gitlab.com`; the GitLab `--repo` is
the project path, subgroups included. For Asana the `intake` profile
key reads `{"adapter": "asana-tasks", "project": GID, "tag": NAME}`;
the tag on a task is the approval, the criteria are an "Acceptance
criteria" list in its description (rich text is read as the equivalent
markdown), each approved open sub-task is its own task, and a task with
open sub-tasks is a parent that never runs. `adapters/asana.py
complete` takes the task's GID from the run's `source` for these tasks.

`--repo-config` names a profile file; its `intake` key
(`{"adapter", "repo", "label"}`) supplies `--repo` and `--label`, and
the adapter refuses a profile that names a different intake adapter.
Point it at a checkout of trunk: `prepare` reads the profile from
trunk, intake reads the file you give it. Or give
`--trunk-repo PATH --trunk BRANCH` instead: intake then reads the
`.t12/project.json` committed at the tip of BRANCH in the repository at
PATH, ignoring uncommitted edits, and refuses a missing branch or
profile. The two forms cannot be combined. A label is required, from
either place: anyone can file an issue, and the label is what a
maintainer applies to say the factory may work on it. Only issues
that carry it are read, open ones only, and pull requests are
ignored.

**The issue format.** The body must have a heading named
"Acceptance criteria" (any level, case-insensitive, optionally
followed by a note such as "(Testable)") followed by a `- ` or `* `
list, or a `- [ ]` checklist, until the next heading. Each item is one
criterion. An issue whose body contains `<!--` anywhere is skipped:
the forge hides comment text from the maintainer who applied the
label, and forges disagree on what an odd comment or fence hides, so
intake does not guess. Every other line is read, code fences
included; visible text in a code block can only narrow a task.
An issue without such a list is skipped and the reason is printed;
intake never invents criteria.

Intake runs `spec_check` in `common.py`, the rules of
`docs/spec-standard.md` that code can check, and skips an issue that
breaks one, printing every reason: more than 10 criteria (7 to 10 print
a `warning` line and still queue), no `Touches:` line, an "Open
questions" section or `_Open question:` line that is not `none`, or a
parent issue (one with sub-issues). Labelled lines are read anywhere,
only as `Name: value`:

```text
Touches: src/auth, tests/auth
Depends on: #12, gl-14
```

`Touches` becomes the task's `touches` (relative paths, no spaces).
Order comes from the forge's native relations where it has them. On
GitHub intake reads each issue's "blocked by" issues: an open one
becomes `depends_on`, a completed one has landed and is dropped, and
one closed as not planned or in another repository skips the issue. A
`Depends on:` line is refused on GitHub. GitLab and Asana intake read
it, since their native dependencies are paid features; `#N` means
`<prefix>-N` of the same adapter. A malformed value skips the issue,
because dropping it would widen the task.

**Checking a draft.** `python3 adapters/common.py spec-check
[BODY|-] [--forge github|gitlab|asana] [--repo-root DIR]` runs the same
body rules before an issue is filed and prints JSON
`{ok, refusals, warnings, new_files}`; it exits 1 when intake would
refuse the body. `new_files` lists `Touches` paths missing from
`--repo-root`, reported, never refused.

**What the task file holds.** Only `id`, `title`,
`acceptance_criteria`, `source` (`{"adapter", "ref", "url"}`, ref is
`owner/name#number`), `touches`, and `depends_on` when there is one. The adapter
builds it from that list and never copies a key out of an issue, so
`gates`, `required_reviews`, `sandbox_*`, `verify_deps`, budgets,
`roles` and paths cannot arrive through an issue; they come from the
profile on trunk. The issue text is still read by workers as the
task's spec, so who may apply the label is the trust decision.

**Re-running.** A file whose content is unchanged is not rewritten.
An edited issue rewrites its file, unless the task already has a run
in `--home`, which is then left alone. Without `--home` intake cannot
see runs, so pass it whenever a state home exists. A file in the
queue directory is overwritten only if its `source` names the same
adapter and the same `owner/name#number`; ids repeat across repos, so
`gh-1` from another repo is skipped with the reason printed.

**Withdrawing.** After a complete listing, intake deletes each task
file in the queue directory whose `source` names this adapter and
this repo but whose issue was not listed: a maintainer removed the
label, or the issue was closed. It prints `withdrawn <id>: <path>`.
A task with a run in `--home` is left alone. A failed listing
deletes nothing.

**Limits.** The issue author can still edit the body after a
maintainer applies the label, and the next intake run picks up the
edit, unless the task already has a run. Remove the label from an
issue whose author you do not trust to leave it alone. A write error
partway through a run (a full disk, a permission error) can leave
some task files written and others not; run intake again once the
error is fixed.

**Failure.** Intake writes no outcome report, so a failed run must
not look complete. It lists every page before it writes. A 403, 5xx or
transport error on the list, on any page, writes nothing, prints
`error: ... nothing written` to stderr and exits 1. Exit 2 means it
could not run (missing token, no label, unreadable profile).

## Runner — drive runs with no one in the inner loop

```bash
python3 adapters/runner.py --home <state-home> --queue <queue dir> --max 2 \
  [--omp-cmd omp] [--omp-profile t12-worker] [--omp-model <id>] \
  [--role-model ROLE=MODEL]... [--role-thinking ROLE=LEVEL]... \
  [--omp-max-cost <usd>] [--skills-cache <dir>] [--notify-cmd "notify-send t12"] \
  [--lane-cap 4] [--backoff-base 30] [--backoff-max 600]
```

Each pass admits tasks through `bin/t12-queue`, then takes one step
per run from `t12 board --json` (a run with parallel lanes: one per
lane, below). A step that leaves a run where it
was (a refused `act`, a failed gate, a worker that never calls a tool)
is not retried in the same invocation. The runner exits when a pass
moves no run, so run it again from cron or when a task file lands in
the queue.

omp is the only worker: each worker step is one `omp --mode rpc`
session. `--omp-cmd` is split like a shell line (default `omp`),
`--omp-profile` defaults to `t12-worker`, `--omp-model` and
`--omp-max-cost` (USD per session) are passed on when set. The
session's time limit is the run's time left.

**Per-role model and thinking.** `--role-model ROLE=MODEL` gives one
role its own `--model`; every other role keeps `--omp-model`, or omp's
profile default when that is unset. `--role-thinking ROLE=LEVEL` does
the same for `--thinking` (`off`, `minimal`, `low`, `medium`, `high`,
`xhigh`, `max`, `auto`) and has no fallback: an unlisted role passes no
`--thinking`. Both repeat, and ROLE is the role name in `next`
(`analyzer`, `red`, `green`, `refactor`, `reviewer`). A stronger,
slower reviewer:

```bash
python3 adapters/runner.py --home <state-home> --queue <queue dir> \
  --omp-model anthropic/claude-sonnet-4-5 \
  --role-model reviewer=anthropic/claude-opus-4-1 --role-thinking reviewer=high
```

A malformed entry (no `=`, an empty ROLE, MODEL or LEVEL, an unknown
level) or a repeated ROLE exits 2 with the reason, before any run is
touched.

- `tdd-step`, `gate` and `check` run as `t12` commands, with no role
  and no session. No model is called.
- Every other `next` (`analyzer: analyze`, `red: red`, `green: green`,
  `refactor: refactor`, `reviewer: review`) is a worker step. `t12
  context` builds the prompt; the worker finishes by calling one of the
  role's tools. The runner mounts them as omp host tools (`t12
  tool-schema --host-tools`), so the arguments are checked against the
  registry while the model writes, and each call goes to `t12 act
  --parsed-by host-tool`. A call `act` refuses comes back as a tool
  error in the same session, so the model fixes it there; after three
  refusals, or when the run has no operations left, the session ends
  and the run does not move. Every `act` carries `--run-id`, the id the
  run had when the session started: if the run was superseded meanwhile
  (`stop`, then `prepare --supersede`), `act` refuses the call, logs the
  rejection on the archived run's event log, and the runner ends the
  session at once, since no retry can help. The new run is untouched.
- Green and refactor are blind. The runner makes a git worktree of the
  run's worktree under its scratch directory with every file matching
  the profile's `test_globs` deleted (`adapters/workspace.py`), and
  runs omp there. The role cannot read, search for or run a test, and
  the guard denies the shell and anything outside that directory. When
  the role calls `green_ready` or `refactor_done`, the runner exports
  the worktree's change as a patch and passes it to `t12 act --patch`;
  the core applies it to the run's worktree, refuses a patch that
  touches a test or protected path, and runs the red tests and lint.
  The export refuses a patch that created or edited a hidden path, and
  that refusal reaches the model as a tool error. The blind worktree is
  removed after every step. Its prompt leaves out `test_globs`,
  `protected_paths` and the real worktree's path. `escalate` carries
  no patch.
- A run that is `local-verified` and whose profile sets `delivery`
  (`github`, `gitlab` or `bitbucket`) is delivered once per invocation:
  `git push origin <branch>` in the run's worktree, the forge adapter's
  `create-pr` (`create-mr` for GitLab), and `t12 deliver`. The token
  comes from the runner's environment (`GITHUB_TOKEN`, `GITLAB_TOKEN`
  or `BITBUCKET_TOKEN`); if it is missing the runner says which one and
  delivers nothing. A step that fails prints the command that retries
  it, and a `confirmed` delivery is never repeated. An `uncertain` one
  is never repeated either: the runner sends `--notify-cmd` a line that
  names `t12 deliver --resolves`. Without `delivery` it prints that the
  run is ready to deliver by hand. It never merges, approves or closes.
- `human: ...`, `stopped: ...` and a spent budget are never acted on.
  After the last pass the runner prints `waiting <id>: <reason>` for
  each and for each run a step did not move. It runs `--notify-cmd`
  with that line as its last argument. An answer goes in through
  `t12 resume --answer`. A merge is a human's.
- The runner keeps no state. Kill it at any point and start it again:
  it carries on from the run records. A second runner on the same
  state home is refused.

Isolated: the omp `--profile` (own auth, settings and MCP servers),
the owned system prompt `prompts/omp-worker.md`, a built-in tool list
by role kind, and the guard extension `adapters/omp/guard.ts`, which
reads a per-step policy from `$T12_GUARD_POLICY`. Skills (unless the
role has pinned ones, below), rules, context files and other
extensions are off. Never `task`, `ask`, `eval` or `web_search`.

| Kind | Built-in tools | Turns | Guard policy |
|---|---|---|---|
| analyzer | none (`--no-tools`) | 4 | writes nowhere, no shell |
| red | read, grep, find, glob, edit, write, bash | 20 | protected paths read-only |
| green | read, grep, find, glob, edit, write | 20 | test globs and protected paths read-only, no shell, confined to the blind worktree |
| refactor | read, grep, find, glob, edit, write | 15 | as green |
| review | read, grep, find, glob, bash | 30 | writes nowhere |

Hitting a turn cap aborts the session and the log line says
`turn-cap`. Every frame, both directions, goes to
`<scratch>/<task>-<session>.transcript.jsonl`, and the accepted `act`
records it as the `worker-raw-N.txt` evidence of the call.

**Parallel lanes (Phase 9).** After an analyzer list with `units`
fans out, `t12 board --json` has the run row (`unit: null`, `next:
lanes: in flight`, `max_lanes`) and one row per lane that is active,
waiting or paused (`unit: "U1"`, `waiting` set while it waits for its
`after` units). The runner steps lane rows at once in a thread pool:
at most the run row's `max_lanes`, `--lane-cap` (default 4) and, after
a rate limit, one fewer for each one met, never below one. A lane that
finishes a step starts its next while a sibling still runs. Lane rows
take the same vocabulary as the run's: `red: red`, `green: green`,
`refactor: refactor`, `human: answer '<q>'`, `waiting: after U1, U2`,
and `tdd-step: integrate`, which runs `t12 tdd-step --unit U`.
`context`, `tool-schema` and `act` get `--unit U` for a lane row, and
the session runs in the lane's own worktree (`context.worktree`); a
blind worktree is made from that. Integration goes one lane at a time
and run-level steps (`tdd-step`, `gate`, `check`, analyzer, reviewer)
one at a time per run; the `lanes: in flight` row is never stepped
and never counts as stuck. A row is named `task@unit` in the log and
in `waiting ...` lines. A step that did not move a lane is not retried
in this invocation, and a lane that keeps moving cannot starve its
siblings: when nothing is in flight each row gets one turn per pass.

**Rate limits.** When omp gives up retrying a refused request
(`auto_retry_end` with `success: false`), or a prompt ends on an
assistant error naming 429, 529, a rate limit or an overloaded
provider, `adapters/omp_rpc.py` ends the session as `rate-limited`.
The runner treats that as "retry later": it uses no attempt, does not
mark the row stuck, requeues it after `--backoff-base` seconds
(default 30, doubling with each refusal of the same row up to
`--backoff-max`, default 600) and runs the rest of the invocation with
one fewer concurrent step. A row refused more than 6 times in a row
is given up on like a step that did not move. Other omp errors are
not rate limits and are reported as before.

Operator step, once per machine: the `t12-worker` profile starts with
no login. Run `omp --profile t12-worker`, then `/login`, and leave.
Until then a worker session cannot reach a model and the step is
reported as an error.

With `FACTORYBENCH_URL` set, the runner reports each issue-sourced
run as a FactoryBench attempt (`attempt.started`, `pr.linked`,
`attempt.finished`); the factory config version is the turbo-12f
commit plus the omp version.

### Pinned third-party skills

A profile can pin skills (`skills` in `.t12/project.json`, shape in
`docs/generic-factory.md`) to roles. `t12 context` lists, as `skills:
[{name, rev, sha256}]`, the pins that apply to the role on this run:
the profile as frozen at the trunk commit, each pin's `when` read from
the run's base commit (a `package.json` dependency, or a tracked file
matching a glob). The core never fetches anything.

```bash
python3 adapters/skills.py sync --profile-json <repo>/.t12/project.json \
  --cache <state-home>/skills [--update-hashes]
python3 adapters/runner.py ... [--skills-cache <dir>]   # default <state-home>/skills
```

`sync` is the one step that touches the network: it downloads each
pinned `repo@rev` from codeload, keeps only the pin's `include` files
under `path` (a `wrap` pin turns one command file into a `SKILL.md`),
installs them as `<cache>/<name>@<rev>/<name>/`, and refuses a tree
whose hash differs from the pinned `sha256` (`--update-hashes` writes
the new hashes into the profile). Nothing in an archive is executed.

For a worker step whose context has skills, the runner:

1. verifies each cached skill against its pinned hash. A missing or
   changed one refuses the step, with the line `run python3
   adapters/skills.py sync --profile-json <repo>/.t12/project.json
   --cache <dir>`. The run does not move, and the runner does not
   retry it in the same invocation.
2. writes an omp config overlay into its scratch directory and passes
   it as `--config`, instead of `--no-skills`: `skills.customDirectories`
   is the mount dirs, `skills.includeSkills` the names, and every
   other skill provider (`enableAgents*`, `enableClaude*`, `enablePi*`,
   user and project) is off, so a product repo's own
   `.agents/skills/` cannot add one. omp's system prompt lists exactly
   the pins.
3. adds each mount dir to the guard policy's `allow_read`, so the
   confined blind roles can read a skill's reference files.
4. (every session, mounted skills or not) writes `{"dir": "meta",
   "skills": [{name, rev, sha256}], "model": ..., "thinking": ...}` as
   the first line of the transcript, so the evidence of a call says
   which skills, model and thinking level the worker had (`skills` is
   `[]` and the others `null` when none applies).

A role with no applicable pin keeps `--no-skills`.

## Report schema

```json
{"adapter": "github", "task_id": "demo-1", "head": "<40-hex sha>",
 "outcome": "confirmed | rejected | denied | uncertain",
 "ref": "https://.../pr/7", "detail": "...", "checked_at": 1762...}
```

`head` must equal the run's reviewed candidate commit, or
`t12 deliver` refuses it.

## Outcomes

| Outcome | Meaning |
|---|---|
| `confirmed` | Remote state read back and matching. Delivery is done. |
| `rejected` | Remote definitively said no, or remote state definitively does not match. |
| `denied` | The mutation was refused for permission/policy (403 on the create/complete call). |
| `uncertain` | State unknown (timeout, 5xx, a read that failed). Nothing may be assumed. |

Rules: a failed READ is always `uncertain` — a 401/403 or 5xx on
a read is not a denial and not a rejection. A failed MUTATION is
`denied` on 403, `rejected` on 404/422 and other definite 4xx,
`uncertain` on 5xx/timeout.

Forge-specific readings of those rules:

- A 404 on the branch is `rejected` only when the project or repo
  itself reads back. Forges also answer 404 for a project the token
  cannot see, so otherwise it is `uncertain`.
- An existing change request is `confirmed` only when it has the
  reviewed head and targets the run's base branch. The same head on
  another target is `rejected`.
- Bitbucket pull requests report an abbreviated hash, so the Bitbucket
  adapter also requires the branch's full head to equal the reviewed
  commit.

## The uncertain block

An uncertain outcome blocks further delivery for that adapter
until a read-back report is recorded with `--resolves`:

```bash
python3 adapters/github.py create-pr --home <state-home> --task-id <id> --out delivery2.json
bin/t12 deliver --home <state-home> --task-id <id> \
  --adapter github --report delivery2.json --resolves
```

Never retry the action blind after an uncertain outcome — the
first attempt may have landed. The block is per adapter.

## Exit codes

An adapter exits 0 when it wrote a report, whatever the outcome.
Non-zero means the adapter itself could not run (missing token,
missing run record, bad arguments) and no report exists.
