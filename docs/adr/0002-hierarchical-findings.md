# 2. Hierarchical findings via `FindingItem`

- **Status:** Accepted
- **Date:** 2026-08-24

## Context

`Rule.evaluate(ev) -> Finding` returns exactly one result. A rule that checks
thousands of individual assertions — quotations, citations, registered
hypotheses, every metric in an experiment manifest — has nowhere structured to
report the individual outcomes.

The observed workaround is to aggregate everything into one parent `Finding` and
write the per-assertion detail to a sidecar file from inside `evaluate`. That
exists only because the core API cannot carry the detail, and it conflicts with
the rule-purity contract in ADR 0004.

## Decision

Keep `Rule.evaluate(ev) -> Finding`. Add a non-recursive child model carried on
the parent:

```python
@dataclass(frozen=True)
class FindingItem:
    id: str
    status: Status
    message: str
    confidence: float = 1.0
    locations: tuple[Location, ...] = ()
    remediation: str = ""
    kind: str | None = None
    attributes: Mapping[str, JsonValue] = field(default_factory=dict)
```

`Finding` stays the rule-level verdict and the unit of rule identity, scoring,
category weight, baseline tracking and suppression. `FindingItem` is a structured
observation explaining that verdict, and is never independently scored.

`id` is unique within the parent and stable under reordering; prefer a domain
identity (`citation:10.1234/example`) over a position. Duplicates are rejected at
construction. `kind` is an open string rather than a closed enum, because
external rule packs need domain-specific values. `attributes` carries small
JSON-safe values so integrations do not have to parse prose.

Child statuses use the same five members with the same meanings. A child status
is explanatory: the rule author chooses the parent status according to the rule's
documented semantics.

Extend the existing `Rule.finding(...)` helper with a keyword-only `items`
parameter. Existing positional calls keep working, and a rule that returns one
`Finding` and no items behaves exactly as it does today.

### Alternatives considered

**Returning `list[Finding]`.** Rejected. It would give one rule as many scoring
units as it has observations, so a rule checking 5,000 assertions would carry
5,000 times the weight of its neighbours, and it would multiply baseline and
suppression identity by the same factor.

**A mandatory reducer** deriving the parent status from its children. Rejected as
a requirement: the correct aggregation is domain-specific, and hiding it behind a
generic rule would make parent verdicts unpredictable. A `summarize_items` helper
provides the counts without imposing a policy.

**Embedding an evidence trail in each item.** Rejected. `FindingItem` answers
"which observation explains this verdict?"; the claim trail answers "what
evidence connects this scientific claim to artifacts?". An item may reference a
claim or graph identifier in `attributes` rather than duplicating a second graph.

## Consequences

Integrations no longer need a sidecar to preserve per-item outcomes. Report
persistence stays outside `evaluate`: if adduce writes a detailed report, the
report layer owns the write.

Machine-readable output must serialise items completely. Human output may
summarise large sets, but **no output silently discards children.** Exceeding any
limit fails explicitly.

The supported envelope is **10,000 children**. Aggregation is O(n), duplicate-id
detection uses a set, and items are not eagerly copied into graph nodes, claim
trails or a second intermediate model — only the representation the output
boundary asks for is materialised.

A hard ceiling, if one is needed at all, should come from measurement rather than
intuition: representative collections at 10,000, 50,000 and 100,000 items,
measuring construction memory, serialised size, serialisation time, aggregation
time and human-report rendering. Rejecting non-JSON values and binary blobs is
required regardless.

The purpose of any such bound is to contain accidental or pathological plugin
output. It is not a security boundary, and must not be described as one — see
ADR 0004.

Baselines and suppression stay at the parent level. Stable item ids leave
item-level support possible later without committing to its semantics now.

## Implementation note (2026-08-26)

SARIF's finding-level filter predates `FindingItem`: only `FAIL` and `PARTIAL`
findings become SARIF results (`src/adduce/report/sarif.py`), and this is
applied before a finding's items are considered at all. A `PASS`, `UNKNOWN` or
`NOT_APPLICABLE` finding is not emitted to SARIF, so its items never reach that
format either — they are outside SARIF's scope entirely, not truncated from a
finding SARIF did report. JSON carries every item of every finding without
exception, matching the Consequences above. The pre-existing filter is
unchanged by this feature.
