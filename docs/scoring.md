# Scoring

How a repository becomes a number, and what that number does and does not mean.
Ground truth: `src/adduce/scoring.py`, `Status` in `src/adduce/rules/base.py`,
and the rule-evaluation loop in `src/adduce/engine.py`.

Everything before [Proposed changes](#proposed-changes) is `IMPLEMENTED` and
describes the code as it stands; that section carries its own state labels and
is not built. Profiles, suppression, and how an unrated card reads in a report
are in the [CLI reference](cli-reference.md#scoring-profiles-suppression).

## Statuses and their score contributions

Five statuses (`rules/base.py:18-23`). `score_value` is a three-key dict lookup
(`rules/base.py:28`); anything else returns `None`.

| Status | Applicable | Assessed | `score_value` |
| --- | --- | --- | --- |
| `PASS` | yes | yes | `1.0` |
| `PARTIAL` | yes | yes | `0.5` |
| `FAIL` | yes | yes | `0.0` |
| `UNKNOWN` | yes | no | `None` |
| `NOT_APPLICABLE` | no | no | `None` |

`UNKNOWN` and `NOT_APPLICABLE` do not mean the same thing. `NOT_APPLICABLE`
means the check does not apply to this repository; `UNKNOWN` means it applies
and adduce could not determine the answer. They share `None`, so every scoring
path testing `score_value is None` treats them as one state. The Applicable and
Assessed columns are the meanings the statuses carry, not a distinction any
scoring expression makes today.

## Four outcomes for a registered rule

A rule reaches exactly one of four outcomes. The first has two causes, counted
separately.

| Outcome | Cause | Finding | In `considered_rules` | In `evaluated_rules` |
| --- | --- | --- | --- | --- |
| Skipped before evaluation | rule id in the profile's `disabled_rules` (`engine.py:123-125`) | none | no | no |
| Skipped before evaluation | `applies_to(repo)` returned `False` (`engine.py:126-128`) | none | no | no |
| Evaluated, not applicable | `evaluate` returned `NOT_APPLICABLE` | yes | yes | no |
| Evaluated, unassessed | `evaluate` returned `UNKNOWN` | yes | yes | no |
| Evaluated and assessed | `evaluate` returned `PASS`, `PARTIAL` or `FAIL` | yes | yes | yes |

A rule skipped before evaluation produces no `Finding` at all and is invisible
to scoring, to coverage, and to every reporter. **A coverage percentage is
therefore not a statement about every registered rule.** It is computed over the
findings returned; rules that never ran are in neither its numerator nor its
denominator. Measured on adduce's own repository, 9 rules were skipped by
`applies_to` against 69 findings returned.

The two pre-evaluation skips have separate telemetry counters,
`rules.skipped_disabled` and `rules.skipped_inapplicable`. Neither appears in
the score card or in a report body. `--timings` prints both on stderr and adds
them to the `telemetry` block of `--format json`.

## Within a category

For each finding whose status has a `score_value` (`scoring.py:150-155`):

```
earned   += score_value * finding.weight
possible += finding.weight
```

Findings carrying `None` are skipped, so a not-applicable or unassessed check
moves the category in neither direction. `finding.weight` is the integer weight
declared on the rule class; across the shipped rules it spans 1 to 8.

The ratio is then scaled by the profile's category weight
(`scoring.py:158-165`): `CategoryScore.earned` is `earned / possible *
cat_weight`, and `CategoryScore.possible` is `cat_weight`. A category row
reading `8/10` reports category weight, not rule weight.
`CategoryScore.findings` holds every finding in the category, including the ones
the arithmetic skipped. `CategoryScore.percentage` is a computed property
(`scoring.py:28-30`) returning `100 * earned / possible`, or `0.0` when
`possible` is falsy; the division is already guarded, and a category the profile
weights at 0 lands on that guard.

## Across categories

Category weights come from the profile TOML (`src/adduce/profiles/*.toml`); a
category the profile does not name weighs 0. Each surviving category adds its
scaled ratio to `weighted_earned` and its weight to `weighted_possible`, and the
total is `100 * weighted_earned / weighted_possible`, or `0.0` when no category
survived (`scoring.py:166-169`).

Only surviving categories enter `weighted_possible`. That renormalisation is
what keeps an inapplicable category out of the result in both directions: a
repository with no CUDA code is not scored against CUDA determinism, and gains
nothing from its absence either.

## Categories that drop out

One line does this (`scoring.py:156-157`):

```python
if possible == 0:
    continue  # nothing applicable in this category; exclude and renormalise
```

It has two effects: the category is absent from `ScoreCard.categories`, so
terminal, Markdown, LaTeX and JSON output lose it identically; and its weight
stays out of `weighted_possible`. The second effect is correct.

The test it performs is *nothing assessed*, which is wider than nothing
applicable. A category whose findings are all `NOT_APPLICABLE` is legitimately
omitted. A category whose findings are all `UNKNOWN` is applicable and
unanswered, and the same line drops it — removing the question rather than
reporting it.

That second case is latent: reachable by construction, and not exhibited by
adduce's own repository. There, 15 category rows render and one category is
dropped — `Checkpoint & Experiment State`, whose 5 findings are all
`NOT_APPLICABLE`. That omission is the legitimate one.

## Tiers, and when no tier is given

`tier_for` (`scoring.py:112-116`): 85 or above Gold, 70 or above Silver, 50 or
above Bronze, otherwise `Needs work`.

`analysable_lines` is the summed line count of the Python modules the analyzer
parsed (`engine.py:138`). Below `MINIMUM_ANALYSABLE_LINES = 100` the card is
`rated=False` and the tier reads `Unrated (insufficient evidence)`. The score is
still computed and still reported.

The constant is measured, not chosen. Most rules are assertions about code:
given no code, the ones that look for a problem find none and pass, and the ones
that look for an artifact are satisfied by its bare presence. On the corpus, a
repository of plausible-looking but empty files reached 72/100 and Silver on 10
lines, while the smallest real repository carries 1,220. Every value between 15
and 1,220 separates every case measured, so the exact number is not
load-bearing. It is a floor on whether anything can be said, not a defence
against deliberate gaming; padding a file defeats it. Passing
`analysable_lines=None` leaves the card rated, so a plugin or a test scoring
findings on its own is unaffected.

## Coverage, as computed today

```
evaluated_rules  = findings whose status has a score_value
considered_rules = len(findings)
coverage         = 100 * evaluated_rules / considered_rules
```

`ScoreCard.coverage` is a computed property returning `0.0` when nothing was
considered (`scoring.py:50-55`). It surfaces as `evidence_base.coverage_percent`
in `--format json` and in the unrated note in terminal output.

On adduce's own repository the 69 findings returned are 23 `PASS`, 16 `FAIL`,
12 `PARTIAL`, 16 `NOT_APPLICABLE` and 2 `UNKNOWN`, so coverage reads
`51 / 69 = 73.9 %`. The 16 not-applicable findings and the 2 unassessed ones sit
in that denominator together, because a single `score_value is None` test cannot
tell them apart.

## `top_fixes`

`top_fixes(card, limit=5)` ranks findings by the total-score points a fix would
buy: `100 * (1 - score_value) * weight / applicable_weight * cat.possible /
total_possible`, where `applicable_weight` is the assessed weight in that
category and `total_possible` the summed weight of the surviving categories
(`scoring.py:184-199`). Suppressed findings, findings with no `score_value`, and
findings already at `1.0` are skipped. The estimate holds the applicable set
fixed: a change that makes another rule apply, or turns an `UNKNOWN` into an
assessment, moves the denominators too.

## What the number is not

A score summarises detected signals. It is not a statement that the artifact
reproduces, and no coverage figure implies adduce executed anything. Coverage
describes how many applicable checks reached an assessment, not how good the
answers were: high coverage with a low score means adduce could assess nearly
everything and most answers were bad news. See
[Honest limits](honest-limits.md).

## Proposed changes

| Change | State |
| --- | --- |
| Coverage denominator becomes applicable findings rather than returned findings | `PROPOSED` |
| Pre-evaluation skip count reported alongside the outcome counts | `PROPOSED` |
| An applicable but wholly unassessed category is preserved rather than dropped, without admitting its weight to the quality denominator | `PROPOSED` |

All three are recorded in
[ADR 0001](adr/0001-status-applicability-and-assessment-coverage.md). None is
implemented; the sections above describe current behaviour.

Under the proposed denominator, coverage on adduce's own repository moves:

```
old:  51 assessed / 69 returned findings   = 73.9 %
new:  51 assessed / 53 applicable findings = 96.2 %
```

**This is a denominator correction, not improved effectiveness.** adduce does
not assess 22 percentage points more evidence than before: the same 51 checks
reach the same 51 assessments on the same repository. The 16 `NOT_APPLICABLE`
findings never applied and should not have reduced coverage.

Coverage stays count-based. It answers what fraction of applicable checks
reached an assessment, a question about checks rather than about importance;
weights continue to apply to the quality score alone. Measured on adduce's own
repository, weighted coverage would read `157 / 161 = 97.5 %`, a divergence of
1.3 percentage points from the count-based 96.2 %. Rule weights span 1 to 8, so
a repository whose unassessed checks are its heaviest would diverge further.
adduce reports no weighted coverage number.
