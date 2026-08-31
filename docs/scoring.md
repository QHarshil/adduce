# Scoring

How a repository becomes a number, and what that number does and does not mean.
Ground truth: `src/adduce/scoring.py`, `Status` in `src/adduce/rules/base.py`,
and the rule-evaluation loop in `src/adduce/engine.py`.

Every section below describes the code as it stands; there is no forward-looking
section on this page. Profiles, suppression, and how an unrated card reads in a
report are in the [CLI reference](cli-reference.md#scoring-profiles-suppression).

**Provenance of every figure below attributed to adduce's own repository.**
Measured 2026-08-31 against analyzer source tree
`9490666122b9a271113b062d8f1d4f0443fe3af79cd2f9ce6eae44709af1a468`, on CPython
3.14.0, darwin arm64, by `adduce check . --format json` under the default
profile, with no rule plugins installed and the repository's own ignore file
honoured. The telemetry counters below come from the same command with
`--timings` added, which reports them and moves no other figure on the card.
One block governs the page, because dating individual numbers invites a page
whose figures were taken against different trees. The digest, not a commit
hash, identifies what was measured: writing a commit hash into the commit that
carries it changes it. Regenerate the digest with
`python3 corpus/scripts/review_facts.py show --root .`.

This is a dated observation, not a maintained figure. A later tree that reports
different counts does not make it stale; it makes it a record of a tree that no
longer exists. The convention holds wherever a block appears, on this page or
any other: a block names the tree the measurement was taken on, which for a
released tree is normally an ancestor of it. `src/adduce/__init__.py` carries
the version and sits inside the hashed tree, so the release commit alone moves
the digest without touching anything a figure describes, and
[CHANGELOG.md](../CHANGELOG.md) records what changed in between. Figures
attributed to the synthetic corpus, to the pilot corpus, or to a repository
other than this one come from other measurements and are outside the block.

## Statuses and their score contributions

Five statuses (`rules/base.py:29-33`). `score_value` is a three-key dict lookup
(`rules/base.py:38`); anything else returns `None`.

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
`Status.is_applicable` (`rules/base.py:40-47`) and `Status.is_assessed`
(`rules/base.py:49-56`) each test membership of a frozenset of members
(`rules/base.py:59-60`). Neither is derived from `score_value`, and neither may
be: `UNKNOWN` and `NOT_APPLICABLE` both carry `None`, so a `score_value is None`
test cannot separate them. Coverage, the category-retention branch and
`top_fixes` all read the predicates rather than the value.

## Five outcomes for a registered rule

A rule reaches exactly one of five outcomes. Two of them have more than one
cause, counted the same way within an outcome. The last three columns are
the score-card counts each outcome lands in.

| Outcome | Cause | Finding | `considered_rules` | `applicable_rules` | `evaluated_rules` |
| --- | --- | --- | --- | --- | --- |
| Passed over | the rule could not supply its id, category, title, weight and severity (`engine.py:282-292`) | none | no | no | no |
| Skipped before evaluation | rule id in the profile's `disabled_rules` (`engine.py:293-294`) | none | no | no | no |
| Skipped before evaluation | `applies_to(repo)` returned `False` (`engine.py:312-314`) | none | no | no | no |
| Evaluated, not applicable | `evaluate` returned `NOT_APPLICABLE` | yes | yes | no | no |
| Evaluated, unassessed | `evaluate` returned `UNKNOWN` | yes | yes | yes | no |
| Evaluated, unassessed | the engine contained a third-party rule whose `evaluate` misbehaved (`engine.py:152`) | yes | yes | yes | no |
| Evaluated, unassessed | the engine contained a third-party rule whose `applies_to` raised (`engine.py:296-310`) | yes | yes | yes | no |
| Evaluated and assessed | `evaluate` returned `PASS`, `PARTIAL` or `FAIL` | yes | yes | yes | yes |

The rule whose `applies_to` raised never reached `evaluate`. It lands in that
outcome because the run knows it was considered and does not know that it was
inapplicable: a rule that answers `False` leaves the score untouched, and
counting this one there would record an applicability decision it never
reached.

Every outcome but the disabled skip and the pass-over is counted, and those
counts sit together in the JSON report under `evidence_base.rules`
(`scoring.py:88-93`). A rule the profile disabled, and a rule passed over
because it could not identify itself, are counted in no report body at all. On
adduce's own repository the run reported:

```json
"rules": {
  "assessed": 50,
  "unknown": 1,
  "not_applicable": 18,
  "skipped_inapplicable": 9
}
```

`assessed` is `evaluated_rules`; `unknown` and `not_applicable` are the two
differences between the three counts, exposed as properties
(`scoring.py:55-61`). `skipped_inapplicable` is passed into `score()` by the
engine (`engine.py:325`), because the rules it counts left no finding behind to
count from later. The block exists so that a reader who sees 98.0 % coverage can
see why that is not a statement about all 78 built-in rules.

A rule skipped or passed over before evaluation produces no `Finding` at all
and is invisible to scoring, to coverage, and to every reporter's finding list.
**A coverage percentage is therefore not a statement about every registered
rule.** It is computed over the findings returned; rules that never ran are in
neither its numerator nor its denominator. Measured on adduce's own repository,
69 findings were returned and a further 9 rules were skipped by `applies_to`,
accounting between them for all 78 built-in rules.

The two pre-evaluation skips and the pass-over each have a telemetry counter:
`rules.skipped_disabled`, `rules.skipped_inapplicable` and
`rules.skipped_unidentifiable` (`engine.py:284`). `--timings` prints the
counters that fired on stderr and puts that same set in the `telemetry` block
of `--format json`. `rules.skipped_unidentifiable` is telemetry only: it
reaches no score card and no report body, so a rule the run could not identify
is visible in the counters and in the warning it raised, and nowhere else.

One further counter, `rules.degraded`, counts something else: the times the
engine contained a rule that is not one of adduce's own and recorded an
`UNKNOWN` finding under that rule's id in place of the result it did not
produce (`engine.py:152`), whether the rule failed in `applies_to` or in
`evaluate`. A built-in never reaches it, because a built-in that misbehaves
ends the run instead.

**A counter that never fires is absent, not zero.** `Telemetry.count` creates
the key on first increment (`telemetry.py:50-51`), and `snapshot` emits only the
keys present (`telemetry.py:59-66`). Measured on adduce's own repository under
the default profile, which disables no rule, and with no rule plugins installed,
stderr reported `rules.skipped_inapplicable: 9` and said nothing about disabled
or degraded rules, and the only `rules.*` keys in the JSON block were
`rules.evaluated` and `rules.skipped_inapplicable`. Read the block with
`.get(name, 0)`: indexing `counters["rules.skipped_disabled"]`,
`counters["rules.skipped_unidentifiable"]` or `counters["rules.degraded"]`
raises `KeyError` rather than returning 0. `rules.skipped_unidentifiable`
cannot fire at all unless a rule pack is installed, so a consumer that indexes
it directly breaks on the ordinary case rather than on a rare one.
In-process, `Telemetry.counter` already returns 0 for a name that never fired
(`telemetry.py:53-54`), so the two access paths disagree and only the JSON one
can raise.

The inapplicable count additionally reaches the score card, at
`evidence_base.rules.skipped_inapplicable`; the disabled and unidentifiable
counts appear in neither the score card nor any report body.

**Two counters share the word "evaluated", and they count different things.**
`evidence_base.evaluated_rules` is the number of rules that reached an
assessment. The telemetry counter `rules.evaluated` (`engine.py:204`) is the
number of rules whose own `evaluate` returned a usable finding. That is not the
same as the number of findings on the card: where the engine contained a
misbehaving third-party rule it supplied that finding itself, so
`rules.evaluated` plus `rules.degraded` is what the finding count comes to. On
adduce's own repository the same run reported 50 for the first and 69 for the
second. They were already distinct quantities; before the coverage change the
denominator happened to carry the telemetry counter's value, so the two at
least met in one fraction. Coverage is now assessed over applicable — 50 over
51 in that run — and they meet nowhere. Read the field names, not the shared
word.

## Within a category

For each finding whose status has a `score_value` (`scoring.py:185-190`):

```
earned   += score_value * finding.weight
possible += finding.weight
```

Findings carrying `None` are skipped, so a not-applicable or unassessed check
moves the category in neither direction. `finding.weight` is the integer weight
declared on the rule class; across the shipped rules it spans 1 to 8.

A finding's `items` (`rules/base.py:280`) never enter this loop. Each
`FindingItem` carries its own `Status`, but only the parent `Finding`'s status
and weight are counted here, so a finding stays exactly one scoring unit no
matter how many items it carries.

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

On adduce's own repository 15 of the 17 categories rendered a row. The two
absences had different causes, and only one of them was this branch:
`Checkpoint & Experiment State` was dropped here, holding 5 findings that were
all `NOT_APPLICABLE`, which is the legitimate omission. `Notebooks` had no
findings at all, so it never reached the branch. No category on that run was
wholly unassessed, so the retention path was not exercised there.

It is exercised elsewhere. The synthetic corpus holds cases in which a
category's only applicable finding is an `UNKNOWN` and the rest are
`NOT_APPLICABLE`; `Result Reconciliation` is one category that reaches that
state. Every such category was previously dropped from the score card, and every
one is now reported. `tests/test_scoring.py` pins the branch itself, so it stays
exercised whatever the corpus holds.

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
(`report/terminal.py:84-111`). With `total is None` the score cell reads
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
exits 1 (`cli.py:400-405`). A gate that cannot be evaluated is not a gate that
passed.

`analysable_lines` is the summed line count of the Python modules the analyzer
parsed (`engine.py:324`). Below `MINIMUM_ANALYSABLE_LINES = 100`
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

On adduce's own repository the 69 findings returned were 20 `PASS`, 12
`PARTIAL`, 18 `FAIL`, 18 `NOT_APPLICABLE` and 1 `UNKNOWN`. 51 of those were
applicable, 50 of the 51 reached an assessment, and coverage read
`50 / 51 = 98.0 %`. The single `UNKNOWN` finding was the whole of the shortfall.

### The denominator changed, not the analysis

```
old:  50 assessed / 69 returned findings   = 72.5 %
new:  50 assessed / 51 applicable findings = 98.0 %
```

**This is a denominator correction, not improved effectiveness.** The same 50
checks reached the same 50 assessments on the same repository, and not one
additional piece of evidence was assessed. What changed is that the 18
`NOT_APPLICABLE` findings left the denominator, where they never belonged: a
check that does not apply to a repository is not a check adduce failed to answer
about it. adduce does not assess 25.6 percentage points more evidence than it
did before. A coverage figure recorded before this change and one recorded
after it are not comparable, and neither number says anything about how good
the answers were.

### Coverage stays count-based

Coverage answers what fraction of applicable checks reached an assessment. That
is a question about checks, not about importance, so weights stay out of it and
continue to apply to the quality score alone.

Weighted coverage — assessed weight over applicable weight — is a measurement
kept in the backlog, not a second metric. **adduce reports no weighted coverage
number.** Measured on adduce's own repository it would have read
`156.0 / 157.0 = 99.4 %`, a divergence of 1.3 percentage points from the
count-based 98.0 %. Rule weights span 1 to 8, so a repository whose unassessed
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
