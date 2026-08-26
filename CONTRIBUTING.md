# Contributing to adduce

Thank you for considering a contribution. This document covers the workflow
and the design constraints that keep the tool trustworthy.

## Development setup

```bash
git clone https://github.com/QHarshil/adduce
cd adduce
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,release]"
pytest --cov=adduce --cov-report=term-missing --cov-fail-under=85
ruff check src tests scripts corpus/scripts bench
mypy src/adduce scripts corpus/scripts bench
python -m build
twine check --strict dist/*
```

## Where things are written down

| Question | Document |
| --- | --- |
| How does a check run, end to end? | [`docs/architecture.md`](docs/architecture.md) |
| What may an out-of-tree package depend on? | [`docs/plugin-api.md`](docs/plugin-api.md) |
| How does a repository become a number? | [`docs/scoring.md`](docs/scoring.md) |
| Why is it built this way? | [`docs/adr/0000-index.md`](docs/adr/0000-index.md) |

[`docs/extending.md`](docs/extending.md) is the worked plugin example;
`docs/plugin-api.md` is the contract it is written against. The full index is
[`docs/index.md`](docs/index.md).

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

## Architecture decision records

A record states one decision, the context that forced it, and the consequences
accepted. Open one when a change alters the public extension API or the scoring
or report contract. Ordinary implementation choices do not need one.

A record is not edited to match later reality. If a decision changes, add a new
record that supersedes it and names the one it replaces. The index is
[`docs/adr/0000-index.md`](docs/adr/0000-index.md); add your record to its table
in the same pull request.

## Reporting false positives

Open an issue with a minimal repository layout (file paths plus the relevant
snippets) and the finding you believe is wrong. The
[false-positive form](.github/ISSUE_TEMPLATE/false_positive.yml) asks for those
fields directly. These reports are the most valuable input the project gets.

## Pull requests

- Keep changes focused; one rule or one fix per PR.
- The coverage, Ruff, mypy, build, and Twine checks from the development setup
  must pass.
- New behaviour needs tests; changed behaviour needs updated tests.
- Complete the [pull request template](.github/pull_request_template.md).

Issues use forms: [bug report](.github/ISSUE_TEMPLATE/bug_report.yml),
[false positive](.github/ISSUE_TEMPLATE/false_positive.yml), and
[API change](.github/ISSUE_TEMPLATE/api_change.yml) for anything touching either
entry-point group or an output contract. Reviewers per area are listed in
[`.github/CODEOWNERS`](.github/CODEOWNERS).

## Dependency updates

Dependency updates arrive as Dependabot pull requests
([`.github/dependabot.yml`](.github/dependabot.yml)). They are merged by hand,
never automatically. The validation corpus preregistration records a
`dependency_versions_sha256` over the exact installed dependency set, so a
dependency change is a change to the analyzer under measurement. Review it, and
settle what it costs the corpus, before merging.

## Release scope: 0.2 and 0.3

The split axis is maturity, not subject matter. Productized, validated work
ships in 0.2; open-ended research milestones move to 0.3. 0.2 is not an
API-only release.

In 0.2: the public extension API and its stability policy; scoring and report
correctness; the architecture, plugin and scoring documentation; contributor and
CI infrastructure; and the delivered half of claim extraction, meaning LaTeX and
Markdown candidate extraction, normalisation, duplicate clustering, and partial
metric reconciliation.

Moving to 0.3: the remaining claim-resolution stages, the effectiveness
acceptance criteria, and the preregistered validation report.

0.2 therefore ships with no preregistration lock. That is deliberate: 0.2 makes
no final effectiveness claim, and the first gate in
[`docs/releasing.md`](docs/releasing.md#release-gates) already permits a release
to "document explicitly which validation remains developmental" in place of
completing the corpus and human-review gates.

Claim extraction's current figures are developmental status, not results.
Pooled recall is 141/296 = 47.6% over the 20 labelled pairs. Precision is
552/887 = 62.2% over the 5 of 34 pairs adjudicated so far, with 96
high-confidence false positives pooled and exactly one pair at zero. The
zero-high-confidence-false-positive acceptance criterion is not met, and no
project document, release note or README line should describe it as met.

## Claiming work and release gates

Substantial work should have a public issue before it is implemented, so the
design can be settled while it is still cheap to change. Open one, or say on an
existing issue that you are working on it. Maintainers coordinate on the issue
when two people are heading for the same change, so the duplicated effort costs
a comment rather than a discarded branch.

Saying so records intent; it does not reserve the work. Release gates are
capability-based: the gate is that the capability exists, is tested, and is
correct, never that a particular contributor opened a pull request. Maintainers
implement whatever a release needs, whenever correctness or release progress
requires it, and fix defects in shipped behaviour without waiting. Where that
overlaps something you have started, say so on the issue and we will settle
which parts land from where.

Maintainer release gates and the Trusted Publishing boundary are documented in
[`docs/releasing.md`](docs/releasing.md). A release tag is not part of the
ordinary contribution workflow.
