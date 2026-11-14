# Reviewer skill

Adapted from super-board's `super-review`, without its host-specific
hooks, board, or merge permissions. Your prompt is
`prompts/reviewer.md`. One reviewer covers quality and security.

## When to use

PR/branch readiness review before merge, or the review lane of an
orchestrator run. Never as the primary implementation workflow — a
requested change goes back through the TDD lane (behaviour via the
analyzer, structure via refactor directives).

## Flow

1. **Scope.** Branch, base, changed files, acceptance criteria,
   `security_paths_touched`. Dirty files unrelated to the task: stop and
   ask.
2. **Your own pass first.** Read the criteria and the raw diff; write 2–4
   hypotheses about what must be true and where it would break. Check them
   against the code and the factory-observed gate results and TDD summary in
   your context, never against the implementer's say-so.
3. **Classify findings** with stable ids and a class:
   - R-ids (quality): Gap, Bug, Verification miss, Scope drift,
     Over-engineering (should fix at most; **never blocks alone**).
   - S-ids (security): Secret, Injection, Auth, Exposure, Dependency.
   Severity: Blocker / Should fix / Nit / Human gate. A Spec-axis finding
   that contradicts a criterion is a Blocker, not a nit. Anything that
   lets an attacker read a secret, run code, or bypass auth is a Blocker.
   **Test-value check:** a test that only passes with the code by
   construction — asserting the implementation's own output or an aliased
   input rather than the criterion — is a Verification miss. A test counts
   when the TDD summary shows the wrong implementation it killed.
4. **Walk the trust boundaries** in the diff: network, files and
   credentials, user input, auth, subprocesses, dependencies. Every path in
   `security_paths_touched` is examined, cleared or raised.
5. **Verify evidence yourself.** Re-run the exact recorded command in the
   worktree. Green continues; red is a finding with the failure quoted. For
   background/upload flows, verify terminal state and saved records, not a
   200. For TypeScript/UI work, also verify the anti-slop Delivery Gate
   report exists and passed.
6. **Write findings so they can be acted on.** A `behaviour` finding states
   an input, what the code does now, and the behaviour it must have: it
   becomes a test-first item. A `structure` finding is one imperative
   sentence: it becomes a refactor directive. A `human` finding names the
   decision. Deepening opportunities (shallow module, seam in the wrong
   place, behaviour spread across call sites) become future tickets — never
   a reason to hold a green candidate.

## Review remembers

A re-review starts from the prior report. Check each prior finding first,
with file:line proof: fixed / not fixed / no longer applies. A "resolved"
note or an implementer's reply is not evidence; read the code. Any not-fixed
finding (other than Over-engineering) comes back with its original id; skip
the fresh pass.

## Report

The `review` tool call carries the report: `verdict`, `findings` (each with
`id`, `class`, `severity`, `where`, `finding`, `owner`) and `verification`
(commands run, results, what you could not check, future tickets).
Verdict mapping:

- `merge-ready` → approve: the run goes to the check step.
- `blocked` → changes requested: findings go to the analyzer.
- `unverified` → changes requested: say what evidence is missing.
- `human-gated` → the run pauses for a human with a named unblock step.

Only say `merge-ready` when review evidence is clean and verification
passed. If evidence is missing, say what is unverified.

## Sessions

You are a separate worker session from the analyzer, red, green and
refactor roles. The factory refuses a review from a session already bound to
another role. Never reuse another role's session. A new candidate voids
your review; the next review starts from the voided report (review
remembers) and is bound to the new candidate commit.

## Merge

There is no merge command. If a merge gate is ever added, it must be pinned
to the head SHA reviewed: if the head moved after review, the evidence is
void. Confirm a merge landed by reading the base branch — never trust the
merge command's exit code alone. A human merges.
