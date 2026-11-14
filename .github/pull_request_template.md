## What and why

Closes #

## How I verified it

- [ ] `python3 -m unittest discover -s tests -q`
- [ ] `bun test adapters/omp/guard.test.ts` (if the guard or a role policy changed)
- [ ] `bash scripts/smoke.sh`
- [ ] A live run on a product repo (if worker behaviour changed), recorded in `DESIGN.md`

## Checklist

- [ ] The core and the tests still use only the standard library and make no network call
- [ ] `README.md`, `docs/reference.md` and `DESIGN.md` match the new behaviour
- [ ] `CHANGELOG.md` has an entry
- [ ] Breaking change (refuses older runs or removes a command): `!` in the commit type, a `BREAKING CHANGE:` footer, and `VERSION` bumped in `t12/model.py` for a new state shape
