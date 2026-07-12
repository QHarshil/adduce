# Contributing to adduce

Thank you for considering a contribution. This document covers the workflow
and the design constraints that keep the tool trustworthy.

## Development setup

```bash
git clone https://github.com/QHarshil/adduce
cd adduce
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
ruff check src tests
```

## Design constraints

These are load-bearing; pull requests that violate them will be asked to change.

1. **Rules never touch the filesystem.** Rules are pure functions over the
   typed `Evidence` object. If a rule needs new information, extend a
   collector in `src/adduce/evidence/` and add it to the evidence model.
2. **Every finding is honest about confidence.** Static analysis detects
   signals. A rule must return a confidence, and `PASS` messages must be
   phrased as "detected", never as a guarantee.
3. **False positives are bugs.** Every rule declares `applies_to` so that,
   for example, a scikit-learn-only repository is never scored against CUDA
   determinism flags. If your rule can misfire, gate it or lower its
   confidence, and add a regression test for the misfire you fixed.
4. **The default run is diagnostic.** Nothing in the default `adduce check`
   may fail a build; gating is opt-in (`--fail-under`, `--fail-on-regression`).
5. **Scaffolds are non-destructive.** Fixers write new files or append
   clearly separated README sections; they skip existing files.

## Adding a rule

1. Pick the category and an ID (`R-<CAT>-<NNN>`; see `adduce rules` for taken IDs).
2. Implement it in the matching module under `src/adduce/rules/`, register it
   in `BUILTIN_RULES` in `registry.py`.
3. Add tests covering: the pass state, the fail state, at least one partial
   or gated state, and any false-positive case you considered.
4. If the fix is mechanical, add a scaffold under `src/adduce/fixers/` and
   set `fix_command`.

External rule packs do not need any of this: publish a package exposing a
`RULES` iterable under the `adduce.rules` entry-point group.

## Reporting false positives

Open an issue with a minimal repository layout (file paths plus the relevant
snippets) and the finding you believe is wrong. These reports are the most
valuable input the project gets.

## Pull requests

- Keep changes focused; one rule or one fix per PR.
- `pytest` and `ruff check src tests` must pass.
- New behaviour needs tests; changed behaviour needs updated tests.
