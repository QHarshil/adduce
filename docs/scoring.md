# Scoring

How a repository becomes a number, and what that number does and does not mean.
Ground truth: `src/adduce/scoring.py`, `Status` in `src/adduce/rules/base.py`,
and the rule-evaluation loop in `src/adduce/engine.py`.

Every section below describes the code as it stands; there is no forward-looking
section on this page. Profiles, suppression, and how an unrated card reads in a
report are in the [CLI reference](cli-reference.md#scoring-profiles-suppression).

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
and adduce could not determine the answer.

The Applicable and Assessed columns are predicates on the enum, not commentary.
`Status.is_applicable` (`rules/base.py:30-37`) and `Status.is_assessed`
(`rules/base.py:39-46`) each test membership of a frozenset of members
(`rules/base.py:49-50`). Neither is derived from `score_value`, and neither may
be: `UNKNOWN` and `NOT_APPLICABLE` both carry `None`, so a `score_value is None`
test cannot separate them. Coverage, the category-retention branch and
`top_fixes` all read the predicates rather than the value.

## Four outcomes for a registered rule

A rule reaches exactly one of four outcomes. The first has two causes, counted
separately. The last three columns are the score-card counts each outcome lands
in.

| Outcome | Cause | Finding | `considered_rules` | `applicable_rules` | `evaluated_rules` |
| --- | --- | --- | --- | --- | --- |
| Skipped before evaluation | rule id in the profile's `disabled_rules` (`engine.py:124-126`) | none | no | no | no |
| Skipped before evaluation | `applies_to(repo)` returned `False` (`engine.py:127-130`) | none | no | no | no |
| Evaluated, not applicable | `evaluate` returned `NOT_APPLICABLE` | yes | yes | no | no |
| Evaluated, unassessed | `evaluate` returned `UNKNOWN` | yes | yes | yes | no |
| Evaluated and assessed | `evaluate` returned `PASS`, `PARTIAL` or `FAIL` | yes | yes | yes | yes |

Every outcome but the disabled skip is counted, and those counts sit together
in the JSON report under `evidence_base.rules` (`scoring.py:88-93`). A rule the
profile disabled is counted in no report at all. On adduce's own repository:

```json
"rules": {
  "assessed": 51,
  "unknown": 2,
  "not_applicable": 16,
  "skipped_inapplicable": 9
}
```

`assessed` is `evaluated_rules`; `unknown` and `not_applicable` are the two
differences between the three counts, exposed as properties
(`scoring.py:55-61`). `skipped_inapplicable` is passed into `score()` by the
engine (`engine.py:141`), because the rules it counts left no finding behind to
count from later. The block exists so that a reader who sees 96.2 % coverage can
see why that is not a statement about all 78 built-in rules.

A rule skipped before evaluation produces no `Finding` at all and is invisible
to scoring, to coverage, and to every reporter's finding list. **A coverage
percentage is therefore not a statement about every registered rule.** It is
computed over the findings returned; rules that never ran are in neither its
numerator nor its denominator. Measured on adduce's own repository, 69 findings
were returned and a further 9 rules were skipped by `applies_to`, accounting
between them for all 78 built-in rules.

The two pre-evaluation skips also have telemetry counters,
`rules.skipped_disabled` and `rules.skipped_inapplicable`. `--timings` prints
the counters that fired on stderr and puts that same set in the `telemetry`
block of `--format json`.

**A counter that never fires is absent, not zero.** `Telemetry.count` creates
the key on first increment (`telemetry.py:50-51`), and `snapshot` emits only the
keys present (`telemetry.py:59-66`). Measured on adduce's own repository under
the default profile, which disables no rule, stderr reports
`rules.skipped_inapplicable: 9` and says nothing about disabled rules, and the
only `rules.*` keys in the JSON block are `rules.evaluated` and
`rules.skipped_inapplicable`. Read the block with `.get(name, 0)`: indexing
`counters["rules.skipped_disabled"]` raises `KeyError` rather than returning 0.
In-process, `Telemetry.counter` already returns 0 for a name that never fired
(`telemetry.py:53-54`), so the two access paths disagree and only the JSON one
can raise.

The inapplicable count additionally reaches the score card, at
`evidence_base.rules.skipped_inapplicable`; the disabled count appears in
neither the score card nor any report body.

**Two counters share the word "evaluated", and they count different things.**
`evidence_base.evaluated_rules` is the number of rules that reached an
assessment. The telemetry counter `rules.evaluated` (`engine.py:134`) is the
number of rule functions that actually ran, which is every rule that returned a
finding. On adduce's own repository the same run reports 51 for the first and 69
for the second. They were already distinct quantities; before the coverage
change the denominator happened to carry the telemetry counter's value, so the
two at least met in one fraction. Coverage is now 51 over 53 and they meet
nowhere. Read the field names, not the shared word.

## Within a category

For each finding whose status has a `score_value` (`scoring.py:185-190`):

```
earned   += score_value * finding.weight
possible += finding.weight
```

Findings carrying `None` are skipped, so a not-applicable or unassessed check
moves the category in neither direction. `finding.weight` is the integer weight
declared on the rule class; across the shipped rules it spans 1 to 8.

The ratio is then scaled by the profile's category weight
(`scoring.py:217-224`): `CategoryScore.earned` is `earned / possible *
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
total is `100 * weighted_earned / weighted_possible`, or `None` when no category
survived (`scoring.py:225-228`).

Only surviving categories enter `weighted_possible`. That renormalisation is
what keeps an inapplicable category out of the result in both directions: a
repository with no CUDA code is not scored against CUDA determinism, and gains
nothing from its absence either.

## Categories that drop out

A category contributing no assessed weight reaches `if possible == 0`
(`scoring.py:191`), and what happens next depends on why it got there.

`possible == 0` means *no assessed weight*, which is wider than nothing
assessed: a rule declaring `weight == 0` can return a `FAIL` and still add
nothing to `possible`. Nothing validates the weight a rule class declares — no
shipped rule uses 0, but an out-of-tree pack can — so the zero cannot be read as
evidence that the category went unanswered. The branch asks for that finding
directly instead of inferring it (`scoring.py:204-207`):

```python
any(
    finding.status.is_applicable and not finding.status.is_assessed
    for finding in cat_findings
)
```

- No finding applied and went unanswered. Either every finding is
  `NOT_APPLICABLE`, or everything applicable was assessed and only carried no
  weight. The category is dropped from `ScoreCard.categories` and omitted from
  every report. Keeping the second case would render a category as unassessed
  while it holds a verdict, which is the reading `possible == 0` already has.
- At least one finding applied and reached no assessment — an `UNKNOWN`. The
  category applied and went unanswered, so it is **kept** on the card, carrying
  its full `findings` list with `earned == 0.0` and `possible == 0.0`. Dropping
  it would remove the question instead of reporting it.

In both cases the category's weight stays out of `weighted_possible`. A retained
unassessed category therefore moves no number: `total`, `tier`, `coverage`,
`evaluated_rules`, `applicable_rules` and `considered_rules` are identical
whether it is there or not. Admitting its weight would let a category adduce
could not assess drag the total down as though it had failed.

`possible == 0` is the signal a reporter reads for "nothing assessed here".
Terminal output shows such a category with no score rather than `0/0`.

On adduce's own repository 15 of the 17 categories render a row. The two
absences have different causes, and only one of them is this branch:
`Checkpoint & Experiment State` is dropped here, holding 5 findings that are all
`NOT_APPLICABLE`, which is the legitimate omission. `Notebooks` has no findings
at all, so it never reaches the branch. No category on this repository is wholly
unassessed, so the retention path is not exercised here.

It is exercised elsewhere. Across the 33-case synthetic corpus, **13 categories
in 10 cases** hold at least one `UNKNOWN` and nothing assessed — most of them
`Paper & Artifact Consistency` or `Result Reconciliation` carrying a handful of
`NOT_APPLICABLE` findings and one unanswered check. Every one of those was
previously dropped from the score card, and every one is now reported.

## Tiers, and when no tier is given

`tier_for` (`scoring.py:142-146`): 85 or above Gold, 70 or above Silver, 50 or
above Bronze, otherwise `Needs work`.

`ScoreCard.total` is `float | None` (`scoring.py:36`). It is `None` only when no
category anywhere reached an assessment, so `weighted_possible` is 0 and there
is no fraction to compute. A card on which every finding is a `FAIL` still reads
`0.0`. A measured zero and no measurement are different results, and the type
now says so.

There are two unrated tiers, and they are not interchangeable:

| Tier constant | Value | Reached when | What it reports |
| --- | --- | --- | --- |
| `UNRATED_TIER` (`scoring.py:117`) | `Unrated (insufficient evidence)` | `analysable_lines` below the floor | too little source to judge |
| `UNASSESSED_TIER` (`scoring.py:123`) | `Unrated (nothing assessed)` | `total is None` | source the checks could not assess |

`total is None` is tested first (`scoring.py:230-235`), so a repository that is
both tiny and unassessed reports the unassessed tier. A reader handed the wrong
one of these looks in the wrong place for the cause, which is why they are
separate strings rather than one.

Terminal output prints a note below the panel, and the note follows the tier
rather than restating a cause the tier did not choose
(`report/terminal.py:67-94`). With `total is None` the score cell reads
`no score` and the note is:

```
No tier assigned: no check reached an assessment, so there is nothing to score.
Every check either did not apply to this repository or could not be answered
from the evidence collected.
```

If that same card is also unrated, one sentence is appended rather than
substituted:

```
The analyzer parsed <n> lines of source, itself below the floor for a rating.
```

Thin source is a second fact there, never the stated cause: the card would carry
no score however much source it had. A card that is unrated but does carry a
score keeps its own note, which names the line count and the coverage fraction,
and that text is unchanged.

One behaviour follows from the ordering. A card that is unassessed but rated
printed no note at all before; it now prints the unassessed one.

`--fail-under` does not invent a zero for a card with no score. It reports that
the threshold could not be evaluated because no check reached an assessment, and
exits 1 (`cli.py:392-397`). A gate that cannot be evaluated is not a gate that
passed.

`analysable_lines` is the summed line count of the Python modules the analyzer
parsed (`engine.py:140`). Below `MINIMUM_ANALYSABLE_LINES = 100`
(`scoring.py:139`) the card is `rated=False` and the tier reads
`Unrated (insufficient evidence)`. The score is still computed and still
reported.

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

## Coverage

```
considered_rules = len(findings)
applicable_rules = findings whose status is applicable
evaluated_rules  = findings whose status is assessed
coverage         = 100 * evaluated_rules / applicable_rules
```

The three counts are taken with the status predicates rather than with
`score_value` (`scoring.py:242-244`). `ScoreCard.coverage` is a computed property
returning `0.0` when nothing was applicable (`scoring.py:63-68`). It surfaces as
`evidence_base.coverage_percent` in `--format json` and in the unrated note in
terminal output.

On adduce's own repository the 69 findings returned are 23 `PASS`, 12 `PARTIAL`,
16 `FAIL`, 16 `NOT_APPLICABLE` and 2 `UNKNOWN`. 53 of those are applicable, 51 of
the 53 reached an assessment, and coverage reads `51 / 53 = 96.2 %`. The 2
`UNKNOWN` findings are the whole of the shortfall.

### The denominator changed, not the analysis

```
old:  51 assessed / 69 returned findings   = 73.9 %
new:  51 assessed / 53 applicable findings = 96.2 %
```

**This is a denominator correction, not improved effectiveness.** The same 51
checks reach the same 51 assessments on the same repository, and not one
additional piece of evidence is assessed. What changed is that the 16
`NOT_APPLICABLE` findings left the denominator, where they never belonged: a
check that does not apply to a repository is not a check adduce failed to answer
about it. adduce does not assess 22 percentage points more evidence than it did
before. A coverage figure recorded before this change and one recorded after it
are not comparable, and neither number says anything about how good the answers
were.

### Coverage stays count-based

Coverage answers what fraction of applicable checks reached an assessment. That
is a question about checks, not about importance, so weights stay out of it and
continue to apply to the quality score alone.

Weighted coverage — assessed weight over applicable weight — is a measurement
kept in the backlog, not a second metric. **adduce reports no weighted coverage
number.** Measured on adduce's own repository it would read
`157.0 / 161.0 = 97.5 %`, a divergence of 1.3 percentage points from the
count-based 96.2 %. Rule weights span 1 to 8, so a repository whose unassessed
checks are its heaviest would diverge further. Measurement reopens the question,
not preference: either more than 20 % of measured repositories diverging by more
than 5 percentage points, or a corpus p95 absolute divergence above 10
percentage points. The reasoning is recorded in
[ADR 0001](adr/0001-status-applicability-and-assessment-coverage.md).

## `top_fixes`

`top_fixes(card, limit=5)` ranks findings by the total-score points a fix would
buy: `100 * (1 - score_value) * weight / applicable_weight * cat.possible /
total_possible`, where `applicable_weight` is the assessed weight in that
category and `total_possible` the summed weight of the surviving categories
(`scoring.py:251-266`). Suppressed findings, findings that reached no
assessment, and findings already at `1.0` are skipped. The estimate holds the
applicable set fixed: a change that makes another rule apply, or turns an
`UNKNOWN` into an assessment, moves the denominators too.

## What the number is not

A score summarises detected signals. It is not a statement that the artifact
reproduces, and no coverage figure implies adduce executed anything. Coverage
describes how many applicable checks reached an assessment, not how good the
answers were: high coverage with a low score means adduce could assess nearly
everything and most answers were bad news. See
[Honest limits](honest-limits.md).
