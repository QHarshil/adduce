## What changed, and why

<!-- Two or three sentences. Link the issue if there is one. -->

## Design constraint touched

<!-- Name the numbered constraint from CONTRIBUTING.md this PR touches and say
     how it stays satisfied. Write "none" if it touches none. -->

## Tests

<!-- Which tests were added or updated, and which state each one covers. -->

- [ ] New behaviour has a test; changed behaviour has an updated test.
- [ ] A fixed rule misfire ships with a regression case for that misfire (a test,
      or a repository under `corpus/synthetic/`).
- [ ] Any new signal comes from a collector in `src/adduce/evidence/`, not from a
      filesystem read inside a rule.

## Local gates

- [ ] `pytest --cov=adduce --cov-report=term-missing --cov-fail-under=85`
- [ ] `ruff check src tests scripts corpus/scripts bench`
- [ ] `mypy src/adduce scripts corpus/scripts bench`
- [ ] `python -m build`
- [ ] `twine check --strict dist/*`
