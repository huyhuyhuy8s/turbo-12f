# Security policy

## Supported versions

Only the latest commit on `main` gets fixes. Runs prepared by an older
state version are refused by every command and must be superseded, so
there is no older line to patch.

## Reporting a vulnerability

Report it privately through
[GitHub security advisories](https://github.com/huyle-gradion/turbo-12f/security/advisories/new),
or by email to huy.leminh@gradion.com. Do not open a public issue.

Include the commit you tested, the command or task file that shows the
problem, and what the factory recorded (the run record, the event log,
or the evidence file).

## What counts

turbo-12f's security claims are stated in [`CONTEXT.md`](CONTEXT.md)
("What this does not claim") and in [`DESIGN.md`](DESIGN.md). A report
is in scope when the factory does less than those documents say, for
example when:

- a worker reaches a test file, a protected path or the state home
  that its role should not see or write;
- green or refactor runs a shell command or reads a test through a
  tool the guard (`adapters/omp/guard.ts`) lets through;
- a verification command escapes the Landlock or Docker sandbox, or
  sees a credential the environment should have stripped;
- the core accepts a step, a gate result or a review that its evidence
  does not support, or `t12 verify` passes on an edited event chain;
- an issue body, a task file or a profile on a candidate branch weakens
  the rules a run follows;
- the core makes a network call, or an adapter retries a mutation after
  an `uncertain` outcome.

Out of scope, because `CONTEXT.md` already says the factory does not
claim them: a same-user process that forges files in the state home,
a full rewrite of the event chain from genesis, data leaving over UDP
from inside `t12-cage`, and anything a model decides inside the limits
the core enforces.
