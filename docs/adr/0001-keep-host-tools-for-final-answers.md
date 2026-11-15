---
status: accepted
---

# Keep host tools for the final structured answer; do not adopt BAML

Every worker session ends with one structured call. omp mounts the role's tools as host tools with a JSON Schema built from `t12/tools.py`, and the core checks the call again with `tools.parse`. We tried the alternative, BAML (schema-aligned parsing): the model writes the call as text in BAML's rendered format and `baml-py` parses it. On the analyzer, red and the reviewer it gave no fewer refused calls and cost the same or more, so we keep the host tools. Issue #97 holds the experiment; #98, #99 and #103 built its pieces.

## What was compared

- Arm A is the current path: the final answer is a host tool call with the role's schema.
- Arm B is the BAML path: the same prompt, plus the BAML rendering of the answer format. The reply goes through `experiments/baml/run.py` `submit`, which parses it with `b.parse.<Kind>Answer`, refuses what BAML loosens (two calls, extra keys), and applies `tools.parse`. A refusal goes back to the model as the next message, up to four tries.
- Roles: analyzer, red, reviewer. Red and the reviewer had their real file and shell tools in both arms.
- Models: claude-sonnet-5-5 and claude-haiku-5-5 for analyzer and red; claude-opus-5-5 with high thinking for the reviewer. omp 18.20.2, baml-py 0.226.3.
- Task: the context of #103 (a finished run of this repository). 5 runs per cell.

## Results

Refused is the number of calls the core or BAML refused across the 5 runs. Wall, tokens and cost are medians per run.

| Role | Model | Arm | Accepted | Refused | Wall | Output tokens | Cost |
|---|---|---|---|---|---|---|---|
| analyzer | sonnet-5-5 | A | 5/5 | 0 | 20.2 s | 2790 | $0.062 |
| analyzer | sonnet-5-5 | B | 5/5 | 0 | 27.7 s | 4087 | $0.076 |
| analyzer | haiku-5-5 | A | 5/5 | 0 | 31.0 s | 6454 | $0.005 |
| analyzer | haiku-5-5 | B | 5/5 | 0 | 37.0 s | 9439 | $0.007 |
| red | sonnet-5-5 | A | 5/5 | 0 | 84 s | 1958 | $0.098 |
| red | sonnet-5-5 | B | 5/5 | 0 | 83 s | 1735 | $0.096 |
| red | haiku-5-5 | A | 5/5 | 0 | 95 s | 6212 | $0.009 |
| red | haiku-5-5 | B | 5/5 | 0 | 100 s | 6884 | $0.010 |
| reviewer | opus-5-5 high | A | 5/5 | 0 | 143 s | 5750 | $0.447 |
| reviewer | opus-5-5 high | B | 5/5 | 4 | 211 s | 5952 | $0.456 |

Schema enforcement in the host tool already produced clean calls in every arm A run, so BAML had nothing to repair. On the analyzer it used about 45% more output tokens and cost 20 to 25% more. On red the two arms are within noise.

## Limits

- The four refusals in the reviewer's arm B are mostly the harness. The model ended a turn with prose such as "the full-suite runs are still going", and the one-shot loop took it as the final reply. The runner's nudge loop was not used, so these refusals say little about BAML.
- One reviewer arm B run took 1244 s against a median of 211 s. We did not look into why.
- One task context, 5 runs per cell, and only whether calls were accepted: output quality was not read.
- Arm A used a small driver that validates with `tools.parse`, not the real runner path and `t12 act`.
- BAML's published gains are on weaker models than these two. A model that cannot hold a schema would change the answer.
- The driver scripts and the raw results were not kept. The numbers above are the record.

## Consequences

- `t12/`, `adapters/runner.py` and the guard stay free of BAML.
- Nothing new depends on `baml-py`. The merged `experiments/baml/` (the generator, the committed `tools.baml`, `submit`, and the command line) stays as experiment code, which only works with `baml-py` installed and a generated client. Removing it is a separate decision.
- The question comes back if a role moves to a model that fails the schema often. Then the check is the refused-call count per session on that role, the number the runner already records.
