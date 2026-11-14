# turbo-12f: design

Working name. Local-only rebuild of Turbo's best ideas, strictly on
Dex Horthy's 12-Factor Agents (HumanLayer), adopting the super-board
reviewer workflow. Not the GitHub `huyle-gradion/turbo` repo; the
core never talks to a forge or tracker, and the delivery and intake
adapters (Phase 7) do. Python standard library only.

## What we kept from Turbo (Michael's mapping, treated as opinion)

- **Evidence, not declarations.** A pass is a stored artifact: exact
  argv, exit code, duration, raw output, SHA-256, git revision, test
  blob hash, config digest, provenance `observed-helper-execution`.
  `check` re-reads and re-hashes everything before certifying.
- **Fail closed.** Missing/stale/changed evidence, a moved HEAD, a
  changed test after red, a reused approval on a new candidate:
  stop and explain, never "try your best".
- **Exact revision binding.** Every result names candidate commit,
  base commit, test blob, gate-config digest. A new commit is a new
  candidate; old verification does not carry over.
- **Bounded autonomy.** One task, time limit, operation limit, repair
  cap, explicit scope, disposable worktree, no direct merge to `main`.
- **Separate roles.** No role is the only judge. Analyzer, red, green,
  refactor and reviewer are different sessions of the same model;
  green and refactor never see a test; the reviewer forms its own
  hypotheses before reading anyone's summary.
- **Human authority explicit.** `local-verified` means "the configured
  evidence chain was observed and is internally consistent". It is not
  acceptance, not permission to merge, not a safety claim.
- **Deterministic core, replaceable host.** The core owns state,
  validation, execution, evidence, hashes, git invariants, timeouts,
  budgets. Calling a model, opening a PR, talking to a tracker are
  host adapters, added later, never inside the core.
- **The state machine.** `unadmitted -> ready -> tdd -> gates ->
  review -> local-verified`, plus `paused` and `stopped`.
- **Build order.** Minimal evidence runner first; state machine;
  safe execution; evidence integrity; limits; roles; delivery last.

## What we deliberately dropped / changed vs Turbo

- No Asana, GitHub, Antigravity/`agy`, CI, or delivery code in
  Phase 1. Those are host adapters (Phase 7), not the core.
- No framework, no agent loop that lets the LLM pick the next step.
  The LLM never decides the phase. Control flow is code (Factor 8).
- No thermos-style re-review loop, no deferred-review backlog. A
  review finding either blocks (bounce to the analyzer) or is filed as
  a future ticket; it never silently re-queues.
- Test-only-what-changed is a policy the gates encode: gates are
  explicit commands in the task file, and `check` refuses to certify
  on anything but the recorded evidence.
- The worker is one narrow job, not "the factory". The factory is
  the deterministic shell around it.

## The bottleneck this version is designed against

Your diagnosis from Turbo (2026-11-07), stated again for this
rebuild: delivery already takes mostly a day and still cannot ship
even two issues, and the bottleneck is testing and CI: the time
spent testing, retesting, checking, and verifying. Three mechanisms
make it worse, and this version is built to break all three:

1. **TDD cost per issue.** Every issue pays red/green iterations,
   targeted tests, a full suite on the final candidate, a post-merge
   suite, and CI. On Turbo the post-merge suite alone reached 426
   tests with a ~1,197s (~20 min) wall on main @ d201c7b. That
   was the backstop, not the per-iteration cost. Re-running the full
   suite on every iteration is what made each issue take a day.
   Countermeasure here: the loop runs only what each step needs.
   After green and after refactor the core runs the red tests this
   run created plus lint, never the suite. The existing suite and
   e2e are gates, run once per candidate (again only after a
   repair), and only if the task or profile declares them. `check`
   certifies only the recorded evidence, never a fresh full run.
2. **Tests with no value.** A test written by the agent that
   implements the code proves nothing: it is written to pass with
   the code, so green is the same claim twice. The test and the
   code agree with each other, and neither is checked against the
   acceptance criteria. Countermeasure here: red, green and refactor
   are different sessions, and the implementer is blind. Green and
   refactor work in a worktree with no test file, never run tests,
   and green never sees the acceptance criteria, so neither can write
   code to fit a test it read (`prompts/tdd-green.md`,
   `prompts/tdd-refactor.md`, `skills/orchestrator/SKILL.md`). A test
   only counts if the core saw it fail first, at the assertion the
   red worker named (not a tautology, not an error), and the final
   run shows it passing on the pinned candidate: same command, test
   blobs locked in between, all recorded.
3. **Useless tests accumulate.** Every issue adds tests and almost
   none are ever removed: 283 tests after issues #20 to #22, 409 at
   the consolidation baseline, 426 by d201c7b. Each new test slows
   every future run, so delivery gets worse the more you ship.
   Consolidation issue #91 needed 18+ net removals just to get back
   under 409. Countermeasure here: for every test red states the wrong
   implementation it kills (`defeats`) and calls `no_cheat_left`
   when none is left, so a test with nothing to kill is not written
   (`prompts/tdd-red.md`); the reviewer treats a test that merely
   restates the implementation as a Verification miss
   (`prompts/reviewer.md`, `skills/reviewer/SKILL.md`). The old
   test-writer prompt's standing "combine and remove" rule is gone.

**Still open, not yet built:** there is no hard test budget. Nothing
caps suite time per run, and `cycle_limit` (default 50) caps
red-green-refactor rounds per task, so it bounds new tests per task
only as a runaway guard. The next decision is whether to add a real
budget to the task file (e.g. `max_new_tests`, or a suite-time
ceiling enforced as a gate) or to rely on the reviewer + `defeats`
rule above.

## The 12 factors: how each one is implemented here

| # | Factor | Implementation in turbo-12f |
|---|---|---|
| 1 | Natural language to tool calls | A worker is one omp RPC session. It turns an owned prompt + context into file edits in its worktree and ends by calling one of the role's tools, which the runner mounts as omp host tools with their JSON Schema (`test_list_ready`, `red_test_ready`, `no_cheat_left`, `green_ready`, `refactor_done`, `review`, `escalate`). `t12 act` maps the call to its deterministic step. The worker never merges and never decides the phase: the CLI refuses any step the state machine does not allow. `prompts/*.md`, `t12/tools.py`, `adapters/omp_rpc.py`. |
| 2 | Own your prompts | Prompts are versioned files under `prompts/` (`tdd-analyzer.md`, `tdd-red.md`, `tdd-green.md`, `tdd-refactor.md`, `reviewer.md`, and `omp-worker.md` for what every worker shares), not strings hidden in a framework or in code. The runner hands omp the owned prompt through `--system-prompt` and an isolated `--profile`, with skills, rules, context files and other extensions off. Changing a prompt is a reviewed change to a file. |
| 3 | Own your context window | `t12 context` builds the worker's context deterministically from the run record, per role kind, and nothing else is smuggled in. Each kind gets only its slice: green gets the current item and sanitized failure lines, never the acceptance criteria, the test list or a test path. |
| 4 | Tools are just structured outputs | `t12/tools.py` is the registry: each role kind lists the tools it may call and the type of every arg. The runner mounts them as host tools, so omp checks the arguments while the model writes. `t12 act` stores the raw output as evidence, then validates it again; an unknown tool, a tool outside the role, a missing, extra, or mistyped arg is a `worker-output-rejected` event that costs an operation, never a guess. The refusal reaches the worker as a tool error in the same session. `t12 context` lists the role's tools. |
| 5 | Unify execution state and business state | One run record per task holds both: phase, tdd step, budgets, gates, evidence refs (execution) and task, ACs, candidate commit (business). Plus an append-only `events.jsonl`. State is derived by folding the record, never from a second store. Every frame of a worker's omp session, both directions, is kept as transcript evidence (`worker-raw-N.txt`). |
| 6 | Launch / Pause / Resume with simple APIs | `prepare`, `pause`, `resume`, `status` are plain CLI commands. Resume re-validates the run from disk (HEAD, locked test blobs, evidence hashes) before continuing; drift fails closed. |
| 7 | Contact humans with tool calls | `escalate` is a tool call the worker (or operator) makes. It records the question and pauses the run. `resume` refuses until a human gives `--answer`; the answer is a `human-response` event, and every later `t12 context` carries the question and answer. The factory never auto-answers. |
| 8 | Own your control flow | The state machine in `t12/model.py` is the only control flow. Transitions are enumerated; an invalid transition is an error, not a prompt decision. Workers are bounded by code: a turn cap per role (analyzer 4, red 20, green 20, refactor 15, reviewer 30) makes the runner abort the omp session (`turn-cap`), and the core enforces `attempt_limit`, `cycle_limit`, `repair_cap`, time and operations. A spent cap pauses the run for a human. |
| 9 | Compact errors into context window | Raw failure output lives in evidence; the next worker context gets a compacted summary (last ~40 lines + exit code + which check). For green and refactor the summary is sanitized: only assertion lines, with test paths and test names removed. The full log is never pasted into a prompt. A call the core refuses comes back inside the live session, so the worker fixes it there. |
| 10 | Small, focused agents | One worker = one omp session = one task = one role (analyzer, red, green, refactor, reviewer). Each role has its own prompt and its own session id, and a session serves one role per run. Analyzer, red and reviewer share the run's worktree under a guard policy (analyzer and reviewer write nowhere). Green and refactor work in a blind worktree: a scratch copy of the run's HEAD with every file matching `test_globs` removed, under a guard extension that blocks tool calls before they execute. |
| 11 | Trigger from anywhere, meet users where they are | Entry points are a CLI and a plain JSON task file. No server, no dashboard needed to launch. A chat message, a cron job, or a terminal can all produce the same task file and run the same commands. |
| 12 | Make your agent a stateless reducer | Each CLI invocation is `f(state on disk, event) -> new state on disk`. Nothing lives only in a process's memory between steps. Any step can be re-run from the run record + event log. |

Honourable mention (Factor 13, pre-fetch context): `prepare`
validates the repo, base, branch, and gate commands up front, so a
run never fails halfway for a missing prerequisite it could have
checked at admission.

## Super-board, as adopted (not copied)

Source: `EricTechPro/super-board`, skill `super-review`
(`skills/super-review/SKILL.md`). Turbo's DESIGN-SPEC adapted its
skill ecosystem and reviewer workflow; we adopt the same ideas,
without its host-specific hooks, GitHub Project board, or merge
permissions.

- **Orchestrator dispatches roles and does not implement.**
  `skills/orchestrator/SKILL.md`: analyzer -> red -> green -> refactor
  -> gates -> one reviewer, bounded feedback routing, one lane at a time.
- **Reviewer forms its own hypotheses first.** Read the ACs and the
  raw diff, write 2 to 4 hypotheses about what must be true and where it
  would break, *then* read the worker's summary as claims to check.
  A claim is a lead, not evidence.
- **Findings are classified, with stable ids:** Gap, Bug,
  Verification miss, Scope drift, Over-engineering (should-fix at
  most, never blocks alone). Severity: Blocker / Should fix / Nit /
  Human gate. Deepening opportunities become future tickets, never a
  reason to hold a green PR.
- **Review remembers.** A re-review starts from the prior report:
  each prior finding is re-checked with file:line proof and marked
  fixed / not fixed / no longer applies. A "resolved" thread or a
  worker's reply is not evidence; read the code.
- **Reviewer re-runs verification itself.** It re-runs the recorded
  test command in a clean worktree. A pass continues; a failure is
  quoted in its findings, and the analyzer takes it from there.
- **Merge is pinned to the reviewed head SHA.** The merge gate
  refuses if the head moved after review. In Phase 1 there is no
  merge command at all: `local-verified` is the end of the factory's
  authority. A human merges. (Turbo's DESIGN-SPEC lets child PRs
  merge into a feature branch without human approval; that is a
  Phase 7 delivery decision for Michael to make, not a default here.)
- **Human gates are explicit.** Money, auth, destructive schema,
  live DB, or any product decision the reviewer cannot safely guess
  goes to a human with a named unblock step, and the run pauses.

## TypeScript and UI work: anti-slop

Source: https://github.com/miqdadbadjuber/anti-slop (MIT).
Michael's pointer: Dex Horthy's 12-factor-agents talks frame the same
problem as a "war on slop": unattended agents ship generic UI, hype
copy, and AI-shaped code, and slop compounds because agents imitate
the repo's existing patterns. (Verified: the repo is real, ~8.9k
stars, v3.2.28; what I could not verify from the 12-factor README is
that Dex name-checked this specific repo, so I am recording it as Michael's
pointer, not as a Dex citation.)

- **What it is:** a filter, not a style guide. 40 mandatory rules
  (R-01 to R-40) in three tiers, a Liveliness Toolkit, and a Delivery
  Gate, a mandatory PASS/FAIL report before anything ships. Six
  skills, load only what the task needs: `antislop` (core, always),
  `antislop-ui`, `antislop-copywriting`, `antislop-human`
  (accessibility), `antislop-layoutmobile`, `antislop-code`.
- **When it applies:** any task whose deliverable is TypeScript code
  or UI, which is the likely shape of product work here (TypeScript
  on Node, React + TypeScript with Vite+ for UI/tests, Electron
  shell for Chat Bridge). The Python core of turbo-12f itself does
  not need it; the factory's product work does.
- **How it runs:** two modes, `during` (rules guide the build, the
  session closes with the Delivery Gate report) and `after` (audit
  existing work, numbered findings, fix what the human approves).
  Default for factory work is `during`.
- **The direction rule:** anti-slop removes slop and stops
  there; visual direction comes from a per-product UI direction file
  (also called `DESIGN.md` in that repo). Without it, UI output is
  labelled a draft without direction. That file shares a name with
  turbo-12f's `DESIGN.md` but is a different file with a different
  job. The direction file belongs to the product repo and the task
  contract (Factor 2: own your prompts; the task supplies direction,
  the worker does not invent it).
- **Not vendored:** turbo-12f does not copy the skill in. The worker
  loads it per task: `npx antislop-ai` (installer),
  `npx skills add miqdadbadjuber/anti-slop`,
  `agy plugin install https://github.com/miqdadbadjuber/anti-slop`,
  or the single `antislop.md` file. Pin a version when adopting;
  the repo does not update itself.
- **Reviewer check:** the reviewer verifies the Delivery Gate report
  exists and passed. A missing or failing gate is a Verification
  miss; the run is unverified until it passes.

## Supersession and stop (Phase 2)

Runs are never overwritten and never silently reset. Stopping is an
explicit, recorded act: `stop --reason ...` moves any active phase
(including `paused`) to `stopped` with the reason in the record and
the event log. A stopped run cannot move again.

Starting over on the same task uses supersession, not reset:
`prepare --supersede` requires the existing run to be `stopped`
(active runs are refused with "stop it first"), archives the old run
record and its event log under a timestamped name
(`runs/<id>.superseded-<ts>.json`), removes the old disposable
worktree and factory branch, and records `supersedes` in the new run.
The archived record keeps every SHA and evidence ref, so the old
attempt stays inspectable. Counters (ops, repairs, wall time) start
fresh in the new run.

Resume consistency: `resume` re-validates from disk before returning
to the prior phase: worktree branch, HEAD against the expected
commit for that phase, the locked test blobs, and every recorded evidence
ref re-hashed. Any drift fails closed and the run stays paused.

## Safe execution (Phase 3)

Enforced by the core, deterministically:

- **Forbidden bypass flags.** `--dangerously-skip-permissions`,
  `--no-sandbox`, `--bypass-sandbox` are rejected at admission
  (gate commands) and at launch (every command the runner starts,
  token-based so a path or code string containing the text does not
  trip it). A task that needs one cannot be admitted.
- **Credential stripping.** Every subprocess environment drops
  credential variables (exact names like `GITHUB_TOKEN`,
  `AWS_SECRET_ACCESS_KEY`, plus patterns for `*TOKEN*`, `*SECRET*`,
  `*PASSWORD*`, `*API_KEY*`, `*PRIVATE_KEY*`) and git-redirecting
  variables (`GIT_DIR`, `GIT_WORK_TREE`, `GIT_SSH_COMMAND`,
  `GIT_CONFIG_*`, `PYTHONPATH`, `LD_PRELOAD`, …), and sets
  `GIT_ALLOW_PROTOCOL=none`.
- **Empty verify-home.** Verification commands (red, final, gate,
  probe) run with `HOME` set to an empty, private directory inside
  the state home, `TMPDIR` inside it, and no system git config, so
  no operator credential store or global config is visible.
- **Landlock cage (Linux).** Where the kernel supports it (ABI ≥ 4),
  verification commands run under Landlock: worktree and verify-home
  writable, system dirs read-only, `/dev` device nodes available,
  all TCP bind/connect denied, including loopback. Verified on this
  machine: a sandboxed command cannot write outside the worktree or
  connect anywhere, while normal commands in the worktree are
  unaffected. Git operations (commit, stage, worktree management)
  run outside the cage so the factory can still record evidence.
  Host toolchains outside the system dirs (mise/nvm/asdf installs) are
  readable only when the operator names them in `T12_TOOLCHAIN_DIRS`;
  each observation records the list, and `/` or the home dir itself is
  refused. Found by the first live TypeScript run: vitest under a mise
  node failed with EACCES and the red worker escalated it.
- **Docker adapter.** A task may request `"sandbox_adapter":
  "docker"` (or the operator sets `T12_SANDBOX_ADAPTER=docker`);
  verification commands then run as `docker run --rm --network none`
  with the worktree and the verify-home mounted at their host paths,
  `--user` set to the host uid/gid (so files created in the worktree
  are owned by the host user), and only the verification env passed
  as `-e` flags, never the host's environment, so no secret can
  ride in. The container env adds two factory-controlled values:
  `GIT_ALLOW_PROTOCOL=none` and `PYTHONDONTWRITEBYTECODE=1` (without
  the latter, the verification commands themselves dirty the
  worktree with `__pycache__` and `check` refuses). The image comes
  from the task's `"sandbox_image"` (default `python:3.12-slim`, or
  `T12_SANDBOX_IMAGE`); it must already be present locally:
  `prepare` refuses when the daemon does not answer or the image is
  missing, so there are no implicit pulls mid-run. The adapter is
  chosen at admission and recorded on the run; every verification
  command and every probe uses that adapter, and the manifest lists
  it with the image. Added 2026-11-11. Tested two ways: against a
  fake `docker` stub (`tests/fake_docker.py`), which emulates the
  daemon and stands in for the container's confinement with a
  Landlock cage, and end to end against the real daemon
  (`RealDockerCase`, which runs when Docker answers and `python:3.12`
  is present). The real run reaches `local-verified` with both probes
  passing, and the worktree stays owned by the host user.

The probes attempt the forbidden access and record the result:

- **Required probes** run at `prepare` and on demand (`t12 probe`):
  `forbidden_credential_access` (planted secret env var + sentinel
  file + home credential store must be unreadable) and
  `external_effect_restriction` (network connect + write outside the
  worktree must be denied). Each attempt is recorded as evidence
  (`probe-<name>-output.txt`, `probes.json`); a granted access fails
  the probe and is reported.
- **Adapter detection.** `landlock` on Linux with a new-enough
  kernel, `docker` when the task or environment requests it and the
  daemon answers, `sandbox-exec` detected on macOS but not enforced
  there (unverified), `none` otherwise. When no adapter is available,
  verification runs unwrapped and the probes record the gap. The
  probes' outside-write target is the state home root (not `/tmp`):
  `/tmp` is the container's own ephemeral directory under Docker,
  so writing it would not test confinement at all.
- **Opt-in hard gate.** A task may set `"require_probes": true`;
  then the tdd steps and `gate` refuse until probes have passed for
  the run, and `prepare` stops the run if probes fail at admission.
  Default is off: probes are evidence, and the manifest lists the
  adapter and every probe result.

What this does not claim: `local-verified` never means "isolated".
Isolation is whatever the adapter on this host enforces, and the
manifest says which. Tests that need a loopback server will not
pass under the cage. That is the cage working, and such tests
belong outside the verification gates or in a task that does not
run them sandboxed.

## Evidence integrity (Phase 4)

The run record is a plain JSON file, and a plain file can be
edited. Phase 4 makes edits detectable:

- **Hash-chained event log.** Every event carries a sequence
  number, a link to the previous event's hash, and a digest of the
  run record as it stood after that command (`state_sha`). Editing,
  deleting, or reordering an event breaks the chain; editing the
  run record after the last event breaks the state digest. The
  first event links to a per-task genesis hash. A corrupted log
  fails closed: further commands refuse until the run is superseded.
- **`t12 verify`, the auditor.** Five checks, each PASS/FAIL, exit
  0 only if all pass: the event chain; every evidence ref re-hashed
  (red, final-run, gate and probe observations, and every green and
  refactor test and lint run); the exit codes (a red must have
  failed, the final run, every green and refactor run and every
  passed gate must have exit 0, and the recorded code must agree
  with the stored observation); HEAD pinned to the expected commit
  and the locked test blobs unchanged; the manifest re-hashed
  against its issued hash.
- **Certification checks the chain too.** `check` refuses on a
  broken chain before it looks at any evidence, and `resume`
  refuses on a broken chain before returning to the prior phase.
  `verify_check` also checks the exit-code story: a gate flipped
  to "passed" in the record while its observation shows a failure
  is refused, and a final run recorded against a commit other than
  the candidate is refused.
- **Manifest issuance is recorded.** Writing a manifest appends a
  `manifest` event with its SHA-256 (or the outside path for
  `--out`), so a rewritten manifest is detectable against the log.
  The `check` certification already recorded the manifest hash in
  its `verified` event (the event keeps that name).

What this is not: there is no signing key and no external
notary. Anyone with write access to the state home *and* the
patience to rewrite the whole chain from genesis can forge a
history. The chain detects edits but does not prevent a full
rewrite by the machine's owner. The boundary is tamper evidence
against accidents and a compromised worker, with no tamper-proofing
against the operator. One corollary, found while
testing Phase 6: a later command absorbs a prior record edit into
the chain (its event's state digest is computed over the edited
record), so the chain only detects edits made after the *last*
event. The exit-code and evidence checks still catch a flipped
gate or a changed test blob even after absorption.

## Limits (Phase 5)

The built-in defaults were guesses (3600s, 100 ops, 3 repairs in
Phase 5). Phase 8 raised ops to 300, since one TDD cycle costs about
five operations, and added 50 cycles and 3 attempts.
Phase 5 replaces guessing with measurement:

- **Measurements at terminal transitions.** When a run reaches
  `local-verified`, is stopped, or pauses on the repair cap, the
  record snapshots what it actually used: wall time, ops, repairs,
  gate count. The manifest shows the measured usage next to the
  budget. A run without measurements cannot calibrate anything and
  is reported as such.
- **`t12 report`, budget reporting.** Per-run usage vs budget,
  cap hits (repairs / ops / time), and aggregates over completed
  runs. `--json` prints the same structure for scripts.
- **`t12 calibrate`, defaults from real runs.** From completed
  runs only (`local-verified` with measurements): the recommended
  default for each budget is the p90, rounded up to a sane step
  (60s / 5 ops / 1 repair / 1 cycle / 1 attempt), never below a floor
  (600s / 20 ops / 2 repairs / 5 cycles / 2 attempts). Fewer than 3
  completed runs, it refuses, because one toy run must not set the
  defaults. Runs paused on the repair cap (escalation kind
  `repair-cap`) are censored: their true need is unknown, so they are listed
  as excluded rather than folded into the numbers. Dry run by
  default; `--apply` writes `limits.json` with the recommendation
  and its provenance (sample size, stats, time).
- **Precedence, recorded.** A task's own budgets always win;
  calibrated defaults from `limits.json` apply when the task omits
  a budget; built-ins are the last resort. The run record stores
  `budget_source` (`task` / `calibrated` / `builtin`) so any run
  can say where its limits came from. A malformed `limits.json`
  fails `prepare` closed rather than being silently ignored.

What this does not do: auto-tune anything. Calibration
is an explicit operator act (`calibrate --apply`), and the
recommendation is a starting point the operator can edit, since the
file is plain JSON. Limits are per state home, not per product:
different products' runs should live in different state homes if
their shapes differ.

## Roles (Phase 6, reshaped by Phase 8)

Phases 1 to 5 built the deterministic core; Phase 6 wires the people
(and their agents) into it. The core still makes no model calls.
The roles are separate worker invocations by the orchestrator, and
the core's job is to make the separation checkable:

- **One session per role.** Every role step carries a session id
  (`--session` on `act` and `review`). A session id may serve
  exactly one role per run; the factory refuses a session already
  bound to another role. The red session cannot write green's
  patch, the green session cannot review, the reviewer's session
  cannot do anything else on the run. `gate`, `tdd-step` and
  `check` are run by the core and need no session. The same
  session may repeat the same role (a retry after a refused call, a
  re-review), since that is the same worker continuing.
- **The review is a command, not a comment.** `t12 review --role
  reviewer --session <id> --verdict
  approve|changes-requested --report <file>` runs after all gates
  pass (phase `review`), on the current candidate. The omp worker's
  `review` tool call reaches the same record through `t12 act`:
  `merge-ready` is approve, `blocked` and `unverified` are
  changes-requested, and `human-gated` pauses the run with an
  escalation. The report file is copied into evidence and hashed;
  the review record binds role, session, verdict, candidate commit,
  report ref, and the prior review for that role. `verify` re-hashes
  the report like any other evidence.
- **Check requires approval.** `check` refuses without an
  approving review for every role in the task's `required_reviews`
  (default `["reviewer"]`; a task can add a project review role or
  set `[]` to opt out) on the current candidate. A
  `changes-requested` review newer than the latest approval
  blocks. Evidence failures are still checked first, so a
  tampered gate or test blob surfaces before the review policy.
- **A new candidate voids reviews.** Changes-requested sends the
  run from `review` back to `tdd` (analyzer, review-fix mode), and
  the next `tdd-step` pins a new candidate: gates reset and prior
  reviews are marked voided, kept in the record as the audit trail.
  The next review for the role records the voided report as
  `prior_review`, and `t12 context --role reviewer` surfaces it:
  review remembers is state the factory carries forward, not a
  note the reviewer has to find.
- **One reviewer, not two (D2).** Phases 6 to 7 shipped security as a
  second review role with its own prompt and skill. Phase 8 dropped
  it: `prompts/reviewer.md` absorbed its boundary checklist
  (secrets, injection, auth, exposure, dependencies), and
  `security_review_paths` stays as reviewer context
  (`security_paths_touched`). A project can still add review roles.

What the factory cannot prove: session ids are claims. Two
different ids could in principle be the same worker, and the core
has no way to know. The enforcement is over the claim, and the
claim is recorded. If the host cannot be trusted to hand out
distinct sessions per role, the separation is only as strong as
the host.

## Delivery (Phase 7)

Delivery is where the factory meets the outside world. The split
is strict:

- **The core never touches the network.** It holds no tokens and
  makes no HTTP calls. `t12 deliver --adapter github|gitlab|bitbucket|asana
  --report report.json [--resolves]` only records a report an
  adapter already wrote: it checks the report's shape, pins it to
  the reviewed head, enforces the uncertain block, stores the
  report as hashed evidence, and appends a `delivered` event.
  `verify` re-hashes delivery reports like any other evidence,
  and `status` lists the deliveries.
- **Adapters do the remote work.** `adapters/github.py create-pr`,
  `adapters/gitlab.py create-mr`, `adapters/bitbucket.py create-pr`
  and `adapters/asana.py complete` are standalone stdlib-only
  scripts. Tokens come from the environment (`GITHUB_TOKEN`,
  `GITLAB_TOKEN`, `BITBUCKET_TOKEN`, `ASANA_TOKEN`); the repo comes
  from `--repo`/`--project`, the task's `github_repo`,
  `gitlab_project` or `bitbucket_repo` field, or the origin
  remote; the Asana task from
  `--task` or the task's `asana_task_gid` field. An adapter exits
  0 when it wrote a report, whatever the outcome; a non-zero
  exit means the adapter itself could not run.
- **Outcomes mean what they say.** `confirmed` only when the
  remote state was read back and matches: the PR exists with the
  reviewed head SHA, the Asana task is complete. `rejected` when
  the remote definitively said no (the PR's head is a different
  commit, the branch is not pushed, the task does not exist).
  `denied` when the mutation was refused for permission (403 on
  the create/complete call). `uncertain` when the state is
  unknown (timeout, 5xx, a read that failed).
- **Never a blind retry.** An uncertain outcome blocks further
  delivery for that adapter. The next attempt must be a read-back
  recorded with `--resolves`, not a repeated action, because a blind
  retry after an uncertain mutation can duplicate the effect (a
  second PR, a second comment) or miss that the first one landed.
  The block is per adapter; one adapter's uncertainty does not
  stop the other.
- **Two mislabelings are ruled out.** Both were real findings in
  the original Turbo's integration work. A 401/403 on a READ is
  uncertain, never denied: the adapter could not establish state,
  and denied means a mutation was refused. A 5xx during
  confirmation is uncertain, never rejected: rejected means the
  remote definitively said no. The adapters classify by
  read-vs-mutation, and the tests pin both cases.
- **No merge.** The change-request adapters create the PR or MR
  and read it back; they never merge, and there is no merge command. A human
  merges. Child-PR auto-merge is not a default; enabling it
  would be a delivery decision for the maintainer.
- **The runner delivers when the profile says so.** A profile
  `delivery` key (`github`, `gitlab` or `bitbucket`, validated at
  `prepare` and copied into the run as `delivery_adapter`) tells the
  runner which adapter to call once a run is `local-verified`: push the
  branch, run the adapter, record the report with `t12 deliver`. The
  profile is the only place the choice lives, so a task or an issue
  cannot send a run's branch to another forge. The runner reads the
  token from its own environment and the core still holds none. Each
  failed step prints the command that retries it; a `confirmed`
  delivery is never repeated and an `uncertain` one goes to the
  notifier, for `--resolves`.

What the factory cannot prove: the adapter's report is a claim.
The core checks the report's shape and its head pinning, and the
report is stored as evidence, but if an adapter lies about
having read the remote back, the core cannot tell. The defense is
that the adapter is small, stdlib-only, and its read-back logic
is the part the tests exercise against a fake server; and the
ref (PR URL, Asana permalink) is in the record for a human to
open.

## TDD lane (Phase 8)

Phase 8 replaced the old test-writer / builder lanes (history: the
`red`, `stage`, `replay` and `green` commands, and the QA and security
roles) with one lane run by omp workers. There are no aliases for the
removed commands; a run prepared before it (state version 1) is
refused and must be superseded.

```text
prepare -> tdd [analyzer -> (red -> green -> refactor)* -> final]
  -> gates -> review -> check -> local-verified
```

- **analyzer** turns the acceptance criteria into a list of items
  (`test_list_ready`: text, kind, source, `needed`, covered
  criteria; plus gaps and refactor directives). The core refuses a
  list that leaves any criterion uncovered, or an empty item.
- **red** writes one failing test for the current item
  (`red_test_ready`), or says none is left (`no_cheat_left`). A call
  that adds items (the devil pass, `item_id: "new"`) may change no
  non-test file (stub limit 0): its test must fail against the
  current production code as it is. A cheat that exists only as
  red's own stub is hypothetical; a passing test is rejected as a
  tautology that points at `no_cheat_left`. Red's context carries
  `redundant`, the item texts the analyzer pruned (`redundant: <text>
  (<why>)` gaps), and an add whose text repeats one is refused with a
  clearer message unless the test fails unaided. Live FizzBuzz run:
  the devil pass, with the cheat as a stub, re-added `6`, `10` and
  `30` after the analyzer had pruned them as `3`, `5` and `15` (4
  devil-added cycles).
- **green** makes the test pass with the smallest change
  (`green_ready`), **refactor** changes structure only
  (`refactor_done`). Both work in the blind worktree and answer with
  a patch.
- After green and after refactor the core runs every red test
  created in this run (`test_command`) and `lint_command`. A failure
  returns to the same role with a sanitized error and counts one
  attempt; more than `attempt_limit` pauses the run for a human. A
  refactor failure rolls the refactor patch back first.
- **Refactor skip.** The same run showed 11 refactor steps with no
  change. After an accepted green the core adds its production
  lines (added plus deleted) to `lines_since_refactor`; if no
  directives are pending and the sum is at most
  `refactor_skip_max_lines` (default 5, 0 = never skip), it skips
  refactor: event `tdd-refactor-skipped`, the cycle counts as if
  refactor had run (`cycle += 1`, `cycle_limit` applies), and the
  next step is the one refactor would have led to. Debt accumulates,
  so several tiny greens still trigger a refactor once the sum
  passes the limit; an accepted refactor resets it to 0.
- **final** (`t12 tdd-step`, deterministic, entered when no item is
  left and red's last call was `no_cheat_left`) runs all red tests
  and lint on the tip commit in the verification sandbox and pins
  the candidate.
- **gates** (the existing suite, e2e) then run one at a time. A gate
  failure sends the run back into tdd at green in repair mode
  (`repairs_used += 1`).
- **review**: one `reviewer` for quality and security. Approve
  (`merge-ready`) moves on to `check`. Changes-requested sends the
  findings to the analyzer in review-fix mode: behavioural findings
  become new red-green items, structural findings become refactor
  directives (`repairs_used += 1`). `human-gated` pauses with an
  escalation.
- When `repair_cap` is exceeded the run **pauses** with an escalation
  of kind `repair-cap`. It does not stop; a human answers with
  `t12 resume --answer`.

### Who sees what

| Role | Sees | Does not see | Built-in tools |
|---|---|---|---|
| analyzer | acceptance criteria, mode, findings (gate output or review report), carried work, human answers, file listing, last error | | none |
| red | criteria, the item list with status, the current item, `test_command`, `test_globs`, the analyzer's pruned (`redundant`) items, files, last error | | read, grep, find, glob, edit, write, bash |
| green | the current item (text and `needed`), on a retry the sanitized failing assertion lines, in repair mode the sanitized gate output | tests, test paths and names, the test list, the acceptance criteria, lint config | read, grep, find, glob, edit, write; no shell |
| refactor | production changed files, diff stat, directives; on a retry the lint lines for production files | tests, the test list, the acceptance criteria | same as green |
| reviewer | criteria, the raw diff, the tdd summary (items, what each test defeats, final run), observed gate results, `security_paths_touched`, the prior review | | read, grep, find, glob, bash; writes nowhere |

Never `task`, `ask`, `eval` or `web_search`. Green and refactor also
run with the guard's search block on: while `hidden` is non-empty the
guard refuses grep, find and glob, because their results cannot be
filtered.

### What the core verifies, not the worker

- **Red.** The change includes a test file (matching `test_globs`)
  and `test_file` is one of them. Red may only create test files or
  edit ones created earlier in this run; every other test file is
  read-only to it. Outside test files it may add at most
  `red_stub_max_lines` lines (none when the call adds items). `test_command` runs in the sandbox on
  the commit: it must exit non-zero (a passing test is a
  tautology), the output must match `assertion_pattern`, it must
  name the claimed last-assertion line in the test file, and
  `failure_count_pattern`, when set, must capture `1`. Then the core
  commits and locks the blobs of every test file the run created.
- **Green and refactor.** The core applies the patch to the run
  worktree, and refuses one that touches a `test_globs` path or a
  protected path. It commits, runs the red tests and lint, and on
  failure resets to the last good commit. A step only advances on
  a pass.
- **Final.** All red tests and lint on the tip, in the sandbox,
  against the pinned commit; `touches` and protected paths measured
  from the base and from where the branch leaves trunk.
- **Gates, review, check.** Unchanged in kind: gates run in a fresh
  checkout, the review is bound to the candidate commit, and `check`
  re-hashes the whole chain (`verify_check`).

### Decisions

- **D1.** A gate failure goes back to tdd at green; the repair cap
  pauses the run with an escalation (kind `repair-cap`) instead of
  stopping it.
- **D2.** The `security` role, `prompts/security.md` and
  `skills/security` are gone; the reviewer prompt absorbed them.
  `security_review_paths` stays as reviewer context
  (`security_paths_touched`).
- **D3.** A run paused on the repair cap is censored in
  `limits.collect`: excluded from calibration and listed.
- **D4.** Built-in `op_limit` 300, `attempt_limit` 3,
  `cycle_limit` 50; calibration covers cycle and attempt limits.
- **D5.** The certification event keeps the name `verified`.
- **D6.** `t12/cage.py` and `bin/t12-cage` stay untouched, outside
  the new flow, and are flagged as removable.
- **D7.** Green sees no acceptance criteria and nothing of tests.
- **D8.** `test_command`, `lint_command`, `assertion_pattern`,
  `failure_count_pattern` and `test_globs` live in the project
  profile, so red, green, refactor and final run one command.
- **D9.** A setUp that fails because the code under test does not exist
  yet is not a red. The older flow accepted it; here a red must match
  `assertion_pattern` and show a failure at its named last assertion,
  and an `ImportError` or a missing file does neither. The stub
  budget (`red_stub_max_lines`) is how missing code gets to fail at an
  assertion, and the refusal says so.
- **D10.** No `step_retries` key. The runner returns a refused call to
  the same omp session (at most three refusals, `max_rejections` in
  `omp_rpc`), the core counts a refused step against `attempt_limit`
  (past which the run pauses with an escalation) and every malformed
  call against the operation limit. A runner-side retry
  count would be a second limit that nothing else enforces.
- **D11.** A worker session is bound to the run it started on. The
  runner passes the run's `run_id` to `t12 act`; if the run was
  superseded in the meantime the call is refused, the rejection goes
  on the archived run's event log (the live run's log is not touched)
  and the session ends, because no retry can help.
- **D12.** A `final` refused because a changes-requested review was
  answered with no new commit pauses the run with an escalation of
  kind `no-new-commit` whose question carries the review report. Time
  in `paused` does not count against the time budget, so waiting for
  that answer costs the run nothing.

### What this does not claim

- **omp's footer.** Even with `--system-prompt` and a fresh
  `--profile`, omp appends a `<project-context>` workstation block
  and a short `<critical>` block that tells the agent to verify by
  running tests. The role prompts override the second one in words
  (green and refactor say they cannot run tests); that is an
  instruction, and the guard's shell block is what holds.
- **Blindness relies on the guard and the blind worktree.** The
  scratch worktree has no file matching `test_globs`, and the guard
  hides those paths, keeps protected paths read-only, denies the
  shell and confines writes to the copy. The worktree shares the run
  worktree's git object store, so the tests still exist there: they
  are reachable only through git commands, which green and refactor
  cannot run. Nothing proves the guard has no gap; a tool omp adds
  later is covered only if the guard blocks it by default.
- **Sanitizing is pattern matching.** Green sees the failing
  assertion lines with test paths and names removed. A failure
  message that spells out an expected value passes through: that is
  the spec the worker needs, and it is also the only leak of the
  test's contents.
- **The core cannot prove the test is good.** It proves the test
  failed for the stated reason, at the stated line, and passes on
  the candidate. Whether it is the right test is the reviewer's
  call, and the reviewer is a model.
- **Lint is only as strong as `lint_command`.** With `null`, lint is
  skipped and the manifest says `lint: not configured`.
- **Turn caps count the runner's view.** The runner counts omp's
  `turn_end` events and aborts past the cap; it cannot see inside
  a turn. Behaviour was probed against omp 18.20.0 only.

## Parallel TDD lanes (Phase 9)

Status: built (steps 1 to 6 of the build order below); "As built"
records where the code differs from this text. The serial TDD lane
runs one worker step at a time, even when the analyzer's list covers
modules that do not depend on each other. Starting more agents on
that shape fails, because every piece of tdd state has a single
writer:

- one worktree (`run["worktree"]`), so agents overwrite each
  other's files;
- one tdd record (`step`, `current`, `last_good_commit`), so a red
  for item B refuses because HEAD is not the last good commit after
  item A's green;
- one run lock, held while `test_command` runs, so a second command
  waits up to `LOCK_WAIT_S` (2 s) and then fails as busy;
- one op budget that every agent draws from, so the bill has no
  per-agent bound.

The design gives each agent its own copy of all of this instead of
adding locks (see "separate before serializing"). One canonical
object stays shared, the event chain, and its writes stay serialized
as they are today.

```text
prepare -> tdd [analyzer -> fan-out
                 -> lane U1: (red -> green -> refactor)* -> lane-final -> integrate
                 -> lane U2: (red -> green -> refactor)* -> lane-final -> integrate
                 -> lane U3 (after U1): starts once U1 is integrated
               -> cross-unit refactor (only when needed) -> final]
  -> gates -> review -> check -> local-verified
```

### Units: the analyzer proposes, the core checks

`test_list_ready` gains an optional `units` list. Each item names one
unit.

```json
{"units": [
  {"id": "U1", "owns": ["src/parser.ts"], "tests": ["test/parser.test.ts"], "after": []},
  {"id": "U2", "owns": ["src/format/"], "tests": ["test/format.test.ts"], "after": []},
  {"id": "U3", "owns": ["src/cli.ts"], "tests": ["test/cli.test.ts"], "after": ["U1", "U2"]}
 ],
 "items": [{"text": "...", "unit": "U1", "...": "..."}]}
```

The core refuses the list (`tdd-list-failed`, so the analyzer
retries) when any of these fails:

- `owns` and `tests` are exact files or directories ending in `/`,
  never globs, so overlap can be decided. Two entries overlap when
  they are equal or one is a directory prefix of the other.
- No path is in two units, across `owns` and `tests` together.
- Every `owns` path is inside the task's `touches`. Every `tests`
  path matches `test_globs` and none exists at the base commit, since
  red only creates test files.
- Every item names exactly one declared unit, and every unit has at
  least one item.
- `after` names only declared units and forms no cycle.
- `needed` applies to units as it does to items: each unit beyond
  the first must say why it cannot share a lane. Without `units`, or
  with only one unit, the run takes the serial path, unchanged.

A file two units would both edit (an exports index, `package.json`)
must belong to one of them, and the other names it in `after`. A
unit cannot be "shared": shared writes are the problem this design
removes.

### What each lane owns

| State | Serial run today | Lane |
|---|---|---|
| tdd record | `runs/<task>.json` `tdd` | `runs/<task>/lanes/<unit>.json`, the same shape (items, step, history, locked, counters) |
| lock | `runs/<task>.lock` | `runs/<task>/lanes/<unit>.lock`; red, green, refactor and their test runs take only this |
| branch, worktree | `t12/<task>`, `worktrees/<task>` | `t12/<task>/<unit>`, `worktrees/<task>@<unit>`, forked from the integration tip when the lane starts; `verify_deps` linked the same way |
| blind worktree | one per green/refactor session | the same, made from the lane worktree |
| write scope | task `touches` | the unit's `owns` plus its `tests`; enforced by the core's touches check and the guard's `writable`, not by prompt wording |
| item ids | `T1`, `T2`, ... | `U1-T1`, `U1-T2`, ...; a devil pass in one lane cannot collide with another lane |
| evidence names | `red-3`, `green-3` | `U1/red-3`, `U1/green-3` |
| budget | run `op_limit`, `cycle_limit`, cost | a fixed share, carved at fan-out (below) |

The run record keeps only a lane table: `{unit, status, branch,
fork_commit, tip, merged_commit}`. The core writes it under the run
lock at fan-out and at integration, both short holds. Events still
go to the one hash chain (`runs/<task>.events.jsonl`) under the run
lock: appending is fast, and a single chain keeps `verify` and
`check` unchanged in kind. Each event carries `unit`.

**Locks.** There are three kinds, always taken in this order: lane,
then integration, then run. No command takes them in reverse.

- **Lane lock** (`lanes/<unit>.lock`): held for the whole of a lane
  command (red, green or refactor, including its test runs).
- **Integration lock** (`runs/<task>/integration.lock`): held only by
  `integrate`, for the whole merge and its test runs.
- **Run lock**: held only for short writes: appending an event, or
  updating the lane table. Nothing runs tests under it.

Before this phase `t12/cli.py` `main` took the run lock around every
command with a task id, so a lane command would have held it while
its tests ran and every other lane would have failed as busy after
`LOCK_WAIT_S`. `main` now sends lane commands to their lane lock and
the integration green to the integration lock; the run lock is taken
inside, around the event append and the lane-table write. Readers
never lock lane files: the store writes them atomically, so a reader
sees the old or the new record, never a mix.

### Integration: one deterministic writer

`integrate` is a new deterministic step (`t12 tdd-step --unit U1`;
no model involved). It runs when a lane reaches its own final (no
unchecked item, and red's last call was `no_cheat_left`):

1. Run the lane's red tests and lint on the lane tip, as `final`
   does today, within the lane's own scope.
2. Under the integration lock, `git merge --no-ff` the lane branch
   into the integration branch. Lanes merge in the order they finish;
   two lanes that finish together merge in unit-id order, and the
   order is recorded in the event, so a replay reproduces it. Units
   own disjoint files, so a textual conflict means the disjointness
   check has a bug: the core aborts the merge and pauses the run with
   kind `integration-conflict`, without retrying.
3. Run all red tests created in the run (every lane already merged,
   plus this one) and lint on the merged commit, in the sandbox,
   still under the integration lock only.
4. On a pass, record `merged_commit`. The integration `locked` map
   becomes the union of the lanes' maps. Lanes whose `after` list is
   now fully merged become ready to start.
5. On a failure (a semantic conflict: two units each correct alone,
   wrong together), the integration branch goes back to the commit
   before the merge, and the event records which lanes' tests
   failed. That decides who repairs it (each case uses a repair,
   `repairs_used += 1`; past `repair_cap` the run pauses as today):
   - **Only the merging lane's tests fail:** that lane is reopened at
     green, its branch reset to the merge result so green sees the
     other units' code. Green gets the sanitized failing lines.
   - **Any already-merged lane's test fails:** the merging lane is
     not blamed for finishing second. A single **integration green**
     runs on the integration worktree, on the merge result, allowed
     to write the `owns` of every lane in the merge. The core checks
     it as it checks green, against all merged red tests and lint.
   - **The merging lane's green is refused only for writing outside
     its `owns`:** this means the fix belongs in another unit. The
     core skips the remaining attempts and moves straight to the
     integration green above.

   Only an integration green that fails up to `attempt_limit`
   pauses the run for a human. Integration green is the green role
   with a wider write scope: same prompt, same blindness, same
   guard; only `writable` grows.

When every lane is merged, the run continues on the integration
worktree. A **cross-unit refactor** runs once, with the whole
`touches` scope, if the sum of the lanes' `lines_since_refactor` is
above `refactor_skip_max_lines` or a directive is pending. Then the
existing `final`, the gates and the one reviewer run, unchanged. A
changes-requested review goes back to the analyzer in review-fix
mode, which may declare units again under the same rules.

### The bill

- `max_lanes` (profile/task, default 3) bounds how many lanes run at
  once. The runner keeps a pool of that size. Lanes past the bound,
  or whose `after` list is not merged yet, wait.
- At fan-out the core splits what is left of `op_limit`,
  `cycle_limit` and `--omp-max-cost` (spend is reported, not
  verified) into fixed shares, in proportion to each unit's item
  count, with a remainder kept for integration and the cross-unit
  refactor. Each lane counts its spend in its own file, with no
  shared counter.
- A lane that uses up its share pauses itself (`lane-budget`), with
  an escalation that names the unit. It never borrows from a running
  lane, and the other lanes keep going. The share is a ceiling, not
  an estimate.
- **Reclaim.** When a lane merges, what it left unspent returns to
  the run's pool. Only `integrate`, under the run lock, grants from
  the pool, and only to a lane paused on `lane-budget`, so a lane
  whose devil pass added items is not stranded while a finished
  sibling's share sits unused. Total spend stays bounded by the same
  caps a serial run has.
- **Provider rate limits.** Before this phase neither
  `adapters/omp_rpc.py` nor the runner recognised a rate-limited
  (HTTP 429) or overloaded session: it ended as a non-accepted
  outcome, and the runner marked the task stuck for the rest of the
  invocation. With three lanes on one key that is likely, and it
  would have stopped a lane until a rerun.
  The runner classifies such an outcome as "retry later": no attempt
  is used, the lane goes back in the queue after a backoff, and
  `max_lanes` drops by one for the rest of the invocation. omp's own
  internal retries, if any, are not relied on (not verified).
- The core decides when to split the work and when to start a lane
  (Factor 8). Agents have no `task` tool and never start agents; the
  existing tool deny list already enforces this.

### Pause, resume, stop

- A lane escalation (attempt limit, lane budget, red escalating to a
  human) pauses that lane only. The run's phase stays `tdd`, and
  `board` shows one row per lane. `t12 resume --unit U1 --answer
  ...` resumes it (Factors 6 and 7).
- `stop` stops the run and every lane. The runner kills the lanes'
  omp sessions, and the lane worktrees are disposable, as the run
  worktree is today.
- A crashed runner leaves lane files and branches in place. The next
  invocation reads each lane's step and continues, because each lane
  file is the same state-update function as a serial run (Factors 5
  and 12).

### What this does not speed up

Inside a unit, TDD stays serial. Each green changes the code that
the next red must fail against, so writing reds ahead would bill for
tests the next green makes redundant or invalid. A list with a single
unit (FizzBuzz) gets no faster. The gain is bounded by the longest
chain of units through `after`, plus integration time.

### Risks

- **The analyzer may split wrongly.** Units that look independent
  can still share behaviour through types or runtime state. The
  merged red tests catch this only where some test exercises the
  interaction. The reviewer still sees the whole diff.
- **A semantic conflict can need the other unit's files.** If
  merged U2 breaks a test of U1 and the fix belongs in U1's `owns`,
  U2's green cannot make it. The integration green (step 5 above)
  can, with every merged lane's `owns`; only when it fails does the
  run pause for a human.
- **Nothing checks the analyzer's split ahead of time.** The core
  checks the declared paths, not whether the units really are
  independent. Each run records `integration_failures` (and which
  path through step 5 they took) in `limits` next to the existing
  p90 calibration, and `retro` reports the rate per project. A
  project whose rate is high gets a lower calibrated `max_lanes`;
  at a calibrated 1 the analyzer's `units` are ignored and the run
  takes the serial path.
- **Integration sits on the critical path.** Each merge reruns all
  red tests created so far. The 10-second suite target matters more
  here than in a serial run.
- **Overlap checks cover declared paths only.** A generated file or
  lockfile written as a side effect of a command is outside `owns`
  and is refused by the touches check, as it is today.

### Build order (each step verifiable alone)

1. Lane state in the model: lane file, lane lock, lane table, ids
   and evidence names prefixed with the unit, state version 3. The
   tdd engine functions take `(lane record, lane worktree)` instead
   of reading `run["tdd"]` and `run["worktree"]`. `cli.main` stops
   wrapping lane commands in the run lock; the run lock moves to the
   event append and the lane-table write. The serial run is one
   implicit lane, so its behaviour and tests do not change.
2. Validation of `units` in the analyzer step and fan-out. Tests:
   overlap, prefix overlap, a cycle in `after`, an unknown unit, a
   path outside `touches`, a test path that already exists.
3. `integrate`: integration lock, merge, rerun of the merged tests,
   rollback, blame by failing lane, integration green. Tests: a
   clean merge; the merging lane's own test fails (lane reopened);
   an already-merged lane's test fails (integration green); green
   refused for writing another unit's file (straight to integration
   green); an injected textual conflict that pauses the run; two
   lanes finishing together merge in unit-id order.
4. Budget shares, reclaim, lane pause/resume. Tests: an exhausted
   lane pauses while a sibling completes; the sibling's unspent
   share is granted to the paused lane on merge; the run total never
   exceeds the serial caps.
5. Runner pool, per-lane board rows, rate-limit backoff. Tests with
   `tests/fake_omp.py` and two lanes: the steps interleave, neither
   lane's commits appear in the other's worktree, a lane running
   tests does not make the other lane's `act` fail as busy, the
   event chain verifies, and a fake 429 session uses no attempt and
   lowers the pool size.
6. `integration_failures` in `limits` and `retro`, and calibrated
   `max_lanes`.
7. A live run on a TypeScript project with three units (two
   independent, one `after` both). Wall time and reported cost are
   compared with the same task run serially (`units` omitted), with
   the same breakdown as "Where serial time goes".

### Where serial time goes (measured before building)

From the live FizzBuzz run `fizz-2` (TypeScript, Vitest, ESLint,
tsc; 10 cycles, two reviews, one review-fix round). Sources: event
timestamps in `runs/fizz-2.events.jsonl`, message timestamps and
reported usage in each session transcript, and `duration_s` in each
check's observation. Active time excludes the 24 s the run sat paused
for a human.

| Bucket | Seconds | Share |
|---|---|---|
| model turns, all roles | 276.5 | 61% |
| omp session start and stop in tdd steps, about 3 s × 25 sessions | ~75 | ~16% |
| core checks inside tdd steps (red tests 12.0, green tests 14.5, green lint 9.0) | 35.5 | 8% |
| outside sessions (probes, final, 4 gates, board polling) | 53.2 | 12% |
| unattributed (analyzer and reviewer timing gaps, see limits below) | ~15 | ~3% |
| **active total** | **454.9** | |

Per role, wall time and reported cost (cost is the provider's
usage report, not verified billing):

| Role | Sessions | Wall s | Model s | Cost $ |
|---|---|---|---|---|
| red | 13 | 181.6 | 116.8 | 0.61 |
| green | 10 | 93.7 | 56.3 | 0.22 |
| refactor | 2 | 13.3 | 4.7 | 0.04 |
| analyzer | 2 | 20.0 | n/a (one message) | 0.06 |
| reviewer (opus, high) | 3 | 93.1 | 98.7 | 0.62 |

What follows from it:

- **Model time dominates**, and in a serial run the tdd share of it
  (red plus green, about 173 s) can only be cut by running units at
  the same time. That is the lever this phase pulls, and it pays only
  for lists with more than one unit.
- **Session start is the next cost (~16%).** Each step is a fresh
  omp process, and red writes about 8 to 9k prompt tokens to cache per
  session. Keeping one red session across cycles would cut both, but
  it gives up a fresh, owned context per step (Factor 3) and lets one
  item's reasoning leak into the next. Not proposed; recorded as the
  trade-off.
- **Checks are cheap here (8%).** The 10-second suite target holds,
  so integration reruns are affordable. A slow suite would change
  that.
- **Red is the expensive TDD role**: two thirds of the tdd-role
  model time and 70% of its cost, because each red reads the code
  and the tests and runs the suite before answering.
- Measurement limits: the analyzer's model time reads 0 because its
  session has a single assistant message; the reviewer's wall and
  model sums do not match because one of its three sessions was a
  retry whose time is attributed to the next event. One run of a toy
  task; the shares are a direction, not a calibration.

### As built

Where the code differs from the text above, the code wins:

- **Read-only commands take no lock.** `context`, `status` and
  `tool-schema` (with or without `--unit`) read atomically written
  files and lock nothing, so a reader never fails as busy while a
  lane runs its tests.
- **Shares.** At fan-out the lanes together get 3/4 of the remaining
  `op_limit` and `cycle_limit`, split by item count; the rest plus
  rounding is the reserve for integration and the cross-unit
  refactor. Cost is not carved: an `act` input carries no spend, so
  each lane records `budget.cost_share`, and the per-session
  `--omp-max-cost` stays the runner's bound. A human can resume a
  `lane-budget` pause with `resume --unit U --op-limit N` or
  `--cycle-limit N`.
- **Merge order is enforced.** `integrate` on a lane that is not
  first in order is refused and names the lane that is.
- **Integration green starts from the merge result.** The
  integration branch stays at the merge commit; `pending_merge`
  records the pre-merge tip, and the merge is recorded only when the
  integration green passes. Rollback to the pre-merge tip happens for
  the own-tests route and for conflicts.
- **The scope-refused route** re-merges onto the current tip and
  counts a second repair; it is recorded as one failure with route
  `scope-refused`.
- **Blame is by name.** The core attributes a failure to the merging
  lane only when every failing test name or file belongs to it; any
  other failure goes to the integration green.
- **Review-fix may declare units again**, with new unit ids only
  (reuse is refused as `unit-id`).
- **Lane worktrees get the task's `verify_deps`** as symlinks to the
  run worktree's copies, so red can run the suite in its lane. Each
  path is added to the repository's shared `info/exclude`, so the
  link never shows as a dirty path; that also hides it in the run
  worktree.
- **Rate limits** are detected from omp's frames: `auto_retry_end`
  with `success: false`, or a prompt ending on an assistant message
  with `stopReason: "error"` and an error naming 429, 529, rate
  limit, overloaded or too many requests. A wall-clock timeout while
  omp is still retrying also counts. Runner flags: `--lane-cap`
  (default 4), `--backoff-base` (30 s), `--backoff-max` (600 s); six
  refusals in a row mark the row stuck.
- **Calibration.** `calibrate --apply` writes a calibrated
  `max_lanes` to `limits.json`, and prepare uses it as the default; a
  task or profile value still wins.

Board `next` vocabulary the runner follows:

| Row | `next` | Meaning |
|---|---|---|
| run | `lanes: in flight` | lanes open; never stepped, never stuck |
| run | `green: green` | integration green (run tdd step `integration-green`); context carries `integration: true` and `writable` |
| run | `refactor: refactor` | cross-unit refactor after the last merge |
| run | `tdd-step` | final, after the last merge |
| lane | `red: red`, `green: green`, `refactor: refactor` | the lane's own loop; a reopened lane shows `green: green` |
| lane | `tdd-step: integrate` | first done lane in merge order |
| lane | `waiting: after U1, U2` | dependencies not merged, a done lane behind another in merge order, or `after integration-green` |
| lane | `human: answer '<q>'` | the lane is paused |

### Live comparison (build step 7)

One task, run four times on the same TypeScript project (Vitest,
ESLint, tsc; reviewer on opus with high thinking): `slugify` in
`src/slug.ts`, `countWords` in `src/wordcount.ts`, and `report` in
`src/report.ts`, which uses both. Times are measured from the
analyzer's list to the event in question. Cost is the provider's
reported usage, not verified billing.

| Run | Lanes | List to first candidate (s) | List to verified (s) | Cycles | Sessions | Cost $ |
|---|---|---|---|---|---|---|
| `text-serial` (`max_lanes: 1`) | none | 482 | 513 | 17 | 44 | 1.71 |
| `text-2` (analyzer chose one unit) | none | 462 | 500 | 17 | 44 | 1.69 |
| `text-3` | U1 ∥ U2, then U3 | **317** | 469 | 18 + 2 review-fix | 54 | 2.12 |
| `text-1` (before the two prompt fixes below) | U1 ∥ U2, then U3 | n/a (paused for a human) | n/a (two invocations, ~665 s) | 21 | 65 | 2.73 |

- **Speed.** In `text-3` U1 ran from 13 s to 156 s and U2 from 16 s
  to 224 s, side by side. U3 started at 236 s, after U2 merged, and
  finished at 309 s. The first candidate came about 1.5 times sooner
  than in the two serial runs. The run as a whole was 31 to 44 s
  faster, because its reviewer asked for changes and the review-fix
  round (analyzer, 2 cycles, final, gates, a second review) took
  152 s. The serial reviewers approved on the first round.
- **Cost** was 24% above serial. Part of that is the extra review
  round; the rest is lane overhead: more red sessions per cycle
  (23 for 19 cycles, against 18 for 17) and a refactor per lane.
- **The review finding was a real gap, made easier by lanes.** U3's
  analyzer items all used the title "Hello World", so a `report` that
  skipped `slugify` passed every test. The reviewer caught it with a
  mutant. U3's red sees only U3's items, so nothing in its lane
  pushes it to exercise slugify's harder inputs.
- **Two prompt fixes came out of these runs.**
  - `text-1`: the analyzer gave each unit one exact test file, and
    red, used to writing a new file per test, was refused 8 times as
    `lane-scope` and then escalated. Red's context now carries
    `created_tests`, and the refusal names the file red may extend;
    the analyzer prompt prefers a test directory per unit. `text-3`
    had no `lane-scope` refusal.
  - `text-2`: the analyzer read `touches: null` as "no path is
    inside touches", and read the prompt's "neither needs the
    other's code" as ruling out `report`. The prompt now says
    `touches: null` allows every path and that a dependent part is a
    unit with `after`.
- **Limits.** One task, one run per arm after the fixes. Whether the
  analyzer splits is the model's call and varied between `text-1`
  and `text-2` under the same prompt; one run after the prompt fix
  does not show that it now splits reliably.

## Layout

```text
bin/t12                  launcher (resolves the package from its own path)
bin/t12-wizard           guided setup: setup/take/run/status/answer/deliver/sync
bin/t12-container        same CLI inside docker run: no network, read-only root,
                         paths mounted at their host locations
container/Dockerfile     default t12-container image: python:3.12 + pytest
t12/
  cli.py                 commands: init/prepare/context/act/tdd-step/gate/
                         review/check/manifest/status/pause/resume/
                         escalate/verify/probe/report/calibrate/retro/
                         deliver/board/tool-schema
  delivery.py            delivery outcomes, head pinning, uncertain block
  model.py               phases, transitions, budgets, run record (Factor 5/8/12)
  runner.py              safe subprocess: argv only, timeout, kill group,
                         sanitized env, duration + exit captured
  gitio.py               git invariants: clean tree, blob hash, ancestry,
                         worktrees, commits
  store.py               atomic writes, private perms, symlink rejection
  evidence.py            sha256 refs, re-hash verification, provenance
  gates.py               ordered gate ladder, repair cap
  tdd.py                 the tdd engine: analyzer/red/green/refactor
                         handlers, red verification, final
  globs.py               test-glob matcher (same rules as adapters/workspace.py)
  limits.py              budget reporting, p90 calibration, limits.json
  sandbox.py             forbidden flags, credential stripping, Landlock cage, Docker adapter, probes
  errors.py
prompts/                 owned prompts, versioned (Factor 2): tdd-analyzer,
                         tdd-red, tdd-green, tdd-refactor, reviewer, omp-worker
skills/                  orchestrator, reviewer (super-board roles)
adapters/                delivery and intake adapters (github, gitlab, bitbucket,
                         asana, github-issues, gitlab-issues, asana-tasks) + shared
                         HTTP/outcome/spec-check rules in common.py;
                         runner.py (drives omp workers, delivers), omp_rpc.py,
                         omp/guard.ts (omp worker host),
                         workspace.py (blind worktree), fb_report.py
tests/                   unittest, synthetic git repos, fake HTTP servers, no network, no model
scripts/smoke.sh         happy-path smoke on a disposable repo
examples/                a sample task file
```

## Truth boundaries (carried over from Turbo)

- The factory is not a coding agent and not a sandbox. A worktree
  isolates files, not process privileges. Protections must be
  tested, not inferred from instructions.
- Host declarations (model name, sandbox claim, cost) are not
  verified facts. Spend is reported usage, estimated cost, or
  unknown, and never presented as verified billing.
- `local-verified` is not QC approval or product acceptance.
- Evidence stored outside the state home, or reachable through a
  symlink, is rejected. A same-user process can forge local files;
  this is an honesty and reproducibility layer, not a security
  boundary against a malicious local actor.

## Roadmap (phases from Michael's Turbo mapping)

Entries 1 to 7 are history: they record what was built then, and the
commands and roles they name (`red`, `stage`, `replay`, `green`,
test-writer, builder, QA, security) were replaced in Phase 8, below.

1. **Minimal evidence runner.** This scaffold: init, prepare, gate,
   manifest, status, pause/resume, and the first command lane
   (history: red, stage, replay, green).
2. **State machine hardening: DONE 2026-11-11.** Property tests
   on transitions (`tests/test_model.py`: the matrix is written as
   an independent spec, so weakening a transition fails the test),
   explicit `stop`, supersession instead of unsafe reset
   (`prepare --supersede` archives the old run + events and deletes
   the disposable worktree/branch; active runs must be stopped
   first), and resume re-validates HEAD, test blob, *and* every
   evidence hash before continuing.
3. **Safe execution: DONE 2026-11-11.** Forbidden bypass flags
   (`--dangerously-skip-permissions`, `--no-sandbox`,
   `--bypass-sandbox`) rejected at admission and at launch;
   credential/git-redirecting env vars stripped from every
   subprocess; verification commands run with an empty verify-home
   and, where the OS supports it, under a Landlock cage (worktree +
   verify-home writable, system dirs read-only, all TCP denied;
   verified on this machine: localhost connect and /tmp write both
   denied, normal commands unaffected); two required safety probes
   (`forbidden_credential_access`, `external_effect_restriction`)
   run at `prepare` and on demand via `t12 probe`, recorded as
   evidence; a task can set `require_probes` to hard-gate the lane
   on passing probes (then red/gate/replay).
4. **Evidence integrity: DONE 2026-11-11.** Hash-chained event
   log: every event links to the previous hash, carries a sequence
   number, and records a digest of the run record as it stood after
   that command. `t12 verify` audits the chain, re-hashes every
   evidence ref, checks the recorded exit codes tell the same story
   as the statuses (a flipped gate status with a failing
   observation is refused by `green`, now `check`),
   re-pins HEAD/test-blob, and checks the manifest against its
   issued hash. `check` and
   `resume` refuse on a broken chain; manifest issuance is itself a
   recorded event. No signing key: the chain detects edits but does
   not prevent a full rewrite by the machine's owner.
5. **Limits: DONE 2026-11-11.** Measurements are recorded at
   every terminal transition (wall time, ops, repairs, gates).
   `t12 report` shows per-run usage vs budget, cap hits, and
   aggregates; `t12 calibrate` recommends defaults from the p90 of
   completed runs (floors: 600s / 20 ops / 2 repairs; minimum
   sample 3; repair-capped runs censored, not folded in) and
   writes them to `limits.json` with `--apply`. `prepare` uses a
   task's own budgets first, calibrated defaults next, built-ins
   last, and records which source applied.
6. **Roles: DONE 2026-11-11, reshaped in Phase 8.** Each role runs
   as a separate worker invocation with its own session id. One
   session serves exactly one role per run, and the factory refuses a
   session already bound to another role. Then: the test-writer
   could not stage, the builder could not review. Now: the red
   session cannot write green's patch and the reviewer cannot do
   anything else on the run. `t12 review` records a report as
   evidence, bound to the candidate commit; `check` requires an
   approving review for every role the task lists in
   `required_reviews` (default `["reviewer"]`) on the current
   candidate. A new candidate voids prior reviews and resets gates,
   and the next review starts from the voided report (review
   remembers, via `t12 context --role reviewer`).
7. **Delivery: DONE 2026-11-11.** The core never talks to the
   network and holds no credentials: adapters (`adapters/github.py`,
   `adapters/asana.py`, stdlib-only) do the remote work and write a
   JSON report, and `t12 deliver` records the report as evidence.
   Delivery needs a `local-verified` run with an intact chain and
   HEAD on the candidate, and is pinned to the reviewed head SHA, so
   a report for any other commit is refused. Outcomes are exactly
   `confirmed` (remote state read back and matching), `rejected`
   (remote definitively said no), `denied` (mutation refused for
   permission), `uncertain` (state unknown). An uncertain outcome
   blocks further delivery for that adapter until an explicit
   `--resolves` read-back report arrives, and never retries blindly.
   The adapter never merges; a human merges the PR.
8. **TDD lane: DONE (Phase 8).** The red/stage/replay/green lanes
   and the test-writer, builder, QA and security roles were replaced
   by `prepare -> tdd -> gates -> review -> check`, run by omp
   workers; see "TDD lane (Phase 8)".
9. **Parallel TDD lanes: BUILT, live-tested.** The
   analyzer declares units with disjoint `owns`. Each unit runs as a
   lane with its own record, lock, branch, worktree and budget share.
   One deterministic `integrate` step merges lanes and reruns all red
   tests. See "Parallel TDD lanes (Phase 9)".
