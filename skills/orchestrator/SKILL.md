# Orchestrator skill

You dispatch work; you do not implement it. One step at a time per run,
through the deterministic CLI (`bin/t12`). The CLI owns state, control
flow, evidence, and budgets. You own routing.

To drive runs with no one in the loop, run `adapters/runner.py`
(see `adapters/README.md`). It follows these rules in plain code. omp is
the only worker. This skill is for driving a run by hand.

Many runs may proceed at once, one orchestrator per run. Admit them
through `bin/t12-queue --max N`, never by hand: it starts a task only when
its `depends_on` tasks are merged and its `touches` overlap no unmerged
run. Each command that changes a run holds that run's lock, so a second
command on the same run fails with `busy`; wait for the first and retry.

## Lane order

```text
prepare -> tdd [analyzer -> (red -> green -> refactor)* -> final]
  -> gates -> review -> check -> human merge
```

`t12 status` shows the run's next step; follow it:

| next step | who runs it |
|---|---|
| `analyzer: analyze` | analyzer worker: acceptance criteria -> item list |
| `red: red` | red worker: one failing test for the current item |
| `green: green` | green worker, in a blind worktree: smallest change |
| `refactor: refactor` | refactor worker, in a blind worktree: structure |
| `tdd-step` | deterministic: `t12 tdd-step` runs all red tests and lint on the tip commit in the verification sandbox and pins the candidate |
| `gate` | deterministic: `t12 gate` runs the next pending gate (existing suite, e2e) |
| `reviewer: review` | reviewer worker: quality and security in one review |
| `check` | deterministic: `t12 check` certifies a candidate with an approving review |
| `human: merge` | a human merges; the factory's authority ends at `local-verified` |

Every worker finishes by calling its role's tool: the factory's tools are
registered with the omp worker as host tools with their JSON Schema
(`t12 tool-schema --host-tools` prints them), and the call is the worker's
whole answer. The call's arguments reach the factory unread, as
`t12 act --role <role> --session <id> --input <args.json>`, adding
`--patch <file>` for green and refactor. The factory validates it, stores
it as evidence, and runs the step it maps to: `test_list_ready` records
the list, `red_test_ready` runs red verification, `green_ready` and
`refactor_done` apply the patch and run the tests and lint, `review`
records the review, `escalate` pauses the run. A rejected call comes back
to the worker as a tool error with the reason; the worker fixes exactly
that and calls again, within the attempt limit. To resume an escalated
run, put the human's answer through `t12 resume --answer "<text>"`; never
answer it yourself.

Without `act`, the review step runs `t12 review --role reviewer --session
<id> --verdict approve|changes-requested --report <file>`. `check` refuses
without an approving review for every required role on the current
candidate.

## Blind worktree: green and refactor

Green and refactor never see a test. The runner builds a scratch copy of
the run worktree with every path matching the profile's `test_globs`
removed, runs the omp worker there under a guard (test paths hidden,
protected paths read-only, shell denied, confined to the copy), and exports
the worker's change as a patch with `--patch`. The core applies the patch to
the real worktree, refuses one that touches a test or protected path, runs
every red test created in this run plus the lint command, and either moves
on or rolls back and gives the same role a sanitized error: the failing
assertion lines with test paths and names removed. Never hand green or
refactor the test source, the acceptance criteria or the test list.

## Sessions: one worker per role

- One session per role per run: analyzer, red, green, refactor, reviewer.
  Every `act` and `review` carries its role's session id. One session id may
  serve exactly one role per run — the factory refuses a session already
  bound to another role. The reviewer's session cannot do anything else on
  this run.
- The orchestrator creates a fresh session per role per run and passes its
  id through. Session ids are opaque strings (the host's session ids); the
  factory records the claim and enforces distinctness — it cannot prove two
  different ids are different workers, and says so in the design doc.
- A new candidate voids prior reviews. The next review starts from the
  voided review (`t12 context --role reviewer` shows it) and is bound to the
  new candidate commit.
- A task enters only through `prepare` with a task file: id, title,
  acceptance criteria, base, gates, budgets. If criteria are not verifiable
  without human judgment, stop with a gap report. The factory consumes
  prepared tasks only; it never invents missing sub-tasks.
- Each role gets its own invocation with its owned prompt
  (`prompts/tdd-<role>.md`, `prompts/reviewer.md`) and the minimal context
  from `t12 context --role <role>`. Nothing else is passed in.
- Roles never share a worktree, never read another role's uncommitted
  files, and never coordinate through mutable notes. Handoffs are the run
  record + evidence refs.
- Red writes tests, green writes behaviour, refactor changes structure
  only. Different sessions, same model is fine.

## Bounded feedback

- **Attempts.** A rejected step (red verification, failing tests or lint
  after green or refactor) goes back to the same role with the sanitized
  error. Past `attempt_limit` the run pauses for a human.
- **Gate failure** sends the run back into tdd at green in repair mode with
  the sanitized gate output; it counts one repair.
- **Review changes-requested** sends the findings to the analyzer
  (review-fix mode): behavioural findings become new red-green items,
  structural findings become refactor directives; it counts one repair. A
  `human-gated` review pauses the run with an escalation.
- Repairs count against the repair cap. When the cap is hit the run pauses
  for a human with an escalation of kind `repair-cap` — it does not stop,
  and you do not keep looping.
- A finding that is not a blocker becomes a future ticket. It never
  silently re-queues, and it never holds a green candidate.
- If the same question stalls twice, or no progress is made in a round,
  stop and bring it to the human instead of continuing.

## Human authority

- `local-verified` is the end of the factory's authority. A human merges.
  Child merges without human approval are not a default here; enabling them
  is a delivery decision for the maintainer.
- Human gates (money, auth, destructive schema, live DB, product decisions)
  pause the run via `escalate`. Never guess them.

## Stop conditions

Stop and explain when: admission checks fail, the red test does not fail
properly, the candidate moved after `tdd-step`, evidence hashes mismatch, a
budget is exhausted, or verification is unverified. Never label unfinished
work ready.
