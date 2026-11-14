# Context and truth boundaries

Vocabulary for turbo-12f. The CLI is the authority on what it
enforces; this file states what it does *not* claim.

- **Run**: one task, one run record (`runs/<id>.json`), one worktree,
  one append-only event log (`runs/<id>.events.jsonl`). A command that
  changes a run holds `runs/<id>.lock` for its duration; a concurrent
  command on the same run is refused as busy. A lane command
  (`--unit`) holds its lane's lock instead, and the integration green
  the integration lock; both take the run lock only around each event
  append. `status`, `context` and `tool-schema` take no lock. Different
  runs share nothing but the product repo's `.git` and run in
  parallel.
- **Lane**: one unit of a run split by the analyzer, with its own
  record (`runs/<id>/lanes/<unit>.json`), lock, branch and worktree.
  Only `t12 tdd-step --unit` merges a lane into the run's branch.
- **Evidence**: a stored artifact with path, SHA-256, revision,
  provenance. All evidence lives under the state home, never inside
  the product repo.
- **Candidate**: the commit `t12 tdd-step` pinned at the end of the tdd
  phase. A new commit is a new candidate; verification bound to the
  old one does not carry over.
- **TDD lane**: `prepare -> tdd [analyzer -> (red -> green ->
  refactor)* -> final] -> gates -> review -> check -> local-verified`.
  A gate failure goes back to tdd at green (repair); a review that
  requests changes goes to the analyzer; a spent repair cap pauses the
  run for a human. The older test-writer / builder lanes and the
  `red`, `stage`, `replay` and `green` commands were replaced by it
  (history).
- **Red test**: a test file created in this run, matching the
  profile's `test_globs`, that the core saw fail for the stated
  reason and then locked by blob hash. Only red writes tests.
- **Blind worktree**: the scratch copy of the run worktree green and
  refactor work in, with every file matching `test_globs` removed.
  The runner builds it; the core applies the patch the worker
  exports.
- **Gate**: one ordered check from the task file or profile (the
  existing suite, e2e). A later gate cannot run until the prior gate
  passed on the same candidate. Gates and the final test run refuse a
  dirty worktree or a moved HEAD, then run in a fresh checkout of the
  candidate commit (`verify/<id>`, removed after), so ignored or
  untracked files in the worker's worktree cannot affect them. Red
  does the same on the commit that adds the test. Each gate records
  the commit it ran on. Declared `verify_deps` are the one exception:
  linked in read-only, and named in every observation.
- **local-verified**: the configured evidence chain was observed and
  remains internally consistent. It is not QC approval, product
  acceptance, or permission to merge.
- **Worker**: an LLM invocation for one role on one task: one omp RPC
  session when `adapters/runner.py` drives it. The worker
  is replaceable; the policy/evidence engine is the durable part.
- **Host adapter**: code that calls a model, a tracker, or a forge.
  Adapters live in `adapters/` and report back through `t12 deliver`;
  the core never imports one.

## What this does not claim

- It is not a coding agent and not a sandbox. A worktree isolates
  files, not process privileges.
- Host declarations (model, sandbox, cost) are not verified facts.
  Spend is reported, estimated, or unknown.
- A same-user process can forge local files. This is an honesty and
  reproducibility layer, and offers no security boundary against a
  malicious local actor.
- Candidate binding holds against accidents (uncommitted, ignored,
  or later-written files in a worker's worktree). The core, not the
  worker, commits red's tests and the patches of green and refactor,
  and refuses a patch that touches a test or protected path. Against
  a worker that tampers on purpose it holds only as far as the guard
  and the blind worktree reach.
- Green's and refactor's blindness is a policy, and nothing proves it.
  Their worktree has no test file, and the guard extension
  (`adapters/omp/guard.ts`) hides those paths, keeps protected paths
  read-only, denies the shell and confines writes to the copy, all
  before a tool call runs. The copy shares the run worktree's git
  object store, so the tests still exist there; they are reachable
  only through git commands, which those two roles cannot run. If the
  guard has a gap, or omp adds a tool the guard does not know, that
  claim fails. What green sees of a failure is the assertion lines
  with test paths and names removed; a message that spells out an
  expected value passes through.
- omp still adds its own footer to every session, even under
  `--system-prompt` and a fresh `--profile`: a `<project-context>`
  workstation block and a `<critical>` line telling the agent to
  verify by running tests. The role prompts override that line in
  words; for green and refactor the guard's shell block is what
  holds. The runner records the session's system prompt and tool list
  in the transcript so a reader can see what the worker was given.
- The core proves that a red test failed at the stated line for the
  stated reason and that the red tests and lint pass on the candidate.
  It does not prove the tests are the right ones, and lint is only as
  strong as `lint_command` (`null` means none ran). The reviewer, a
  model, judges the rest.
- Red and the reviewer have a shell. A shell is a process with the
  operator's file privileges, and the guard sees tool calls, not what
  a process does once started. `bin/t12-cage` is optional hardening
  for such a worker; the default flow does not use it and the run
  record does not say whether it was used. If you cage a worker, the
  cage denies every write to the shared `.git` and the state home, so
  it cannot plant a hook, a `git replace` ref, a smudge filter, an
  `info/attributes` line, or a `.pth` file in the verify home. The
  caveats below stay open.
  - Nothing forces the cage. `t12` cannot tell a caged worker from
    an uncaged one. Deferred to
    [#17](https://github.com/huyle-gradion/turbo-12f/issues/17).
  - The worktree's `.git` pointer file is writable inside the cage.
    Only `t12-cage commit` checks it. `t12 act`, `t12 tdd-step` and
    the gates run git in the worktree without that check. Run
    `t12-cage commit` after `t12-cage` ends and before any `t12`
    command, and stop the run when it refuses. Deferred to
    [#17](https://github.com/huyle-gradion/turbo-12f/issues/17).
  - No caged process outlives `t12-cage`, so nothing caged is left
    to rewrite the pointer after the check. `t12-cage` runs the
    command in a new PID namespace, and the kernel kills every
    process in that namespace when `t12-cage` ends: when it returns,
    when a signal stops it, and when it is killed with `SIGKILL`.
    This does not depend on `t12-cage` running any cleanup. Inside
    the namespace no pid names a process outside it, so the worker
    cannot signal `t12-cage` or the operator's other processes or
    set a resource limit on them. It can kill the namespace's first
    process, which ends its own run. The namespace leaves three gaps:
    - After a `SIGKILL` the kernel kills the caged processes a
      moment after `t12-cage` is gone, not before. Wait for the
      worktree to go quiet before `t12-cage commit`. The private temp
      dir is left behind.
    - An unprivileged process gets a PID namespace only with a user
      namespace, so `t12-cage` refuses to start on a host that forbids
      unprivileged user namespaces. The user namespace maps only the
      operator's uid and gid, to themselves. The command holds no
      capability in it, and Landlock still denies every mount.
    - `/proc` is still the host's. The worker can list the
      operator's processes and read their command lines.
  - On a kernel older than Landlock ABI 9 the worker can connect to
    a unix socket outside the cage (the session bus, a container
    daemon) and ask its owner to write for it.
  - A path the operator adds with `--write` is outside the claim. A
    writable agent config directory lets the worker leave
    instructions for the next uncaged agent.
  - The cage limits writes to file contents and directory entries.
    It does not cover metadata: the worker can `chmod`, `utime`, or
    set an xattr on any file the operator owns, the shared `.git`
    included. That changes no content git reads, but it can make a
    file unreadable to the operator.
  - The cage does not stop data from leaving. It limits writes but
    not reads of what it is given, and Landlock restricts TCP only.
    UDP is open with or without `--network`, so the worker can send
    anything it can read to any host, for example in DNS queries.
- No network, no credentials, no model calls in the core or tests.
