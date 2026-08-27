# The Artifact Evidence Graph

The evidence graph is adduce's typed intermediate representation: every fact it
detects, with where the fact came from, how it was established, and how much
that method is worth.

It is offline, deterministic, and diagnostic. Building it changes nothing about
a check, and `adduce graph` reports what was detected — not a certification.

```console
adduce graph                      # counts by node type, and a content id
adduce graph --format json        # the whole graph as canonical JSON
adduce graph --store              # also write nodes.jsonl and edges.jsonl
```

## Why a fact carries its method

The load-bearing field is `resolution_method`. A configuration value read out
of a parsed YAML file is a fact. "This project uses Hydra", inferred from a
`defaults` key sitting next to a directory called `conf`, is not — it is a hint
in the shape of a parse. Both are worth recording; recording them identically
is what makes a tool untrustworthy.

So the vocabulary is closed and the rule is mechanical:

> `confidence` may be `1.0` only when `resolution_method` is `direct_parse` or
> `author_declared`.

Anything else is refused at construction, including a method this build does
not recognise. A future version's method arriving in a stored graph is treated
as an inference until this build knows better, never as a fact.

| method | meaning |
| --- | --- |
| `direct_parse` | read from a parser's output |
| `ast_resolved` | resolved through the Python syntax tree |
| `alias_resolved` | resolved through an import alias |
| `wrapper_resolved` | resolved through a wrapping call |
| `lexical_match` | matched on names or paths |
| `graph_match` | resolved by traversing this graph |
| `numeric_reconciliation` | matched by value, with rounding awareness |
| `author_declared` | stated by the author in the manifest |
| `online_resolved` | resolved against a remote, under `--online` |
| `dynamic_observed` | observed by executing something, under an opt-in layer |
| `model_ranked` | reordered by a model |

`model_ranked` is listed in `UNTRUSTED_METHODS`. It may reorder presentation or
lower a confidence; it may never raise one, and it may never contribute to a
passing verdict.

## Identity

Every node is identified twice, and both are required.

**`logical_id`** is deterministic, legible, and stable across content edits:
`sourcefile:src/train.py`, `configvalue:configs/base.yaml#optim.lr`,
`claim:paper/main.tex#tab2.r3.c2`. It **never encodes a line number**, because
lines move under a reformat and a locator that moves with them is not an
identity. Lines are carried as data, in `locations`.

**`content_id`** is `sha256` over the node's canonical form, excluding
`provenance` and `confidence`. Re-running an unchanged producer over unchanged
bytes therefore yields the same `content_id`, which is what lets a later cache
key on it.

## Uncertainty is a kind, not a gap

`uncertainty` records the shape of what is unknown:

- `unresolvable_dynamic` — e.g. `getattr(module, name)`
- `ambiguous_candidates` — with the candidates named
- `parse_failed`
- `not_examined` — with a reason, e.g. a size limit

`not_examined` is deliberately distinct from absence. A file skipped for size
and a file that does not exist are the same silence otherwise, and a silent
partial result is the failure mode this project exists to avoid.

## Node envelope

The envelope is the **serialization** contract. It is not how a node is held in
memory: materialising it per node was measured at 1,417 bytes against 211 for
the lean in-memory form, which at the scale of a large repository's call sites
is the difference between 522 MB and 78 MB. Nodes are held lean; the envelope
is built at the boundary.

```json
{
  "logical_id": "configvalue:configs/base.yaml#optim.lr",
  "content_id": "sha256:…",
  "type": "ConfigurationValue",
  "schema_version": 1,
  "value": {"key": "optim.lr", "scalar": 0.0003, "canonical_name": "learning_rate"},
  "locations": [{"path": "configs/base.yaml", "line": 12, "end_line": 12}],
  "provenance": {
    "producer": "adduce.aeg.producers.config",
    "producer_version": 1,
    "parser": "yaml.safe_load|json.loads|tomllib.loads",
    "parser_version": "stdlib+pyyaml",
    "resolution_method": "direct_parse",
    "inputs": ["sha256:…"],
    "analyzer_version": "0.1.2"
  },
  "confidence": 1.0,
  "uncertainty": null,
  "owner": "builtin"
}
```

## Node and edge types

Nodes: `PaperClaim`, `MetricDefinition`, `MetricObservation`, `SourceFile`,
`Symbol`, `ExecutionCommand`, `RunScript`, `ConfigurationValue`,
`ConfigurationSnapshot`, `DependencyDeclaration`, `EnvironmentConstraint`,
`DatasetReference`, `ModelReference`, `CheckpointReference`, `SeedOperation`,
`HardwareRequirement`, `RemoteArtifact`, `RepositoryCommit`, `GeneratedResult`.

Edges: `CLAIMS`, `PRODUCED_BY`, `CONFIGURED_BY`, `EXECUTED_BY`, `READS`,
`WRITES`, `DEPENDS_ON`, `DERIVED_FROM`, `VERSIONED_AT`, `REPORTED_IN`,
`EVALUATED_ON`, `PINNED_TO`, `CONTRADICTS`, `MAY_SUPPORT`.

`CONTRADICTS` and `MAY_SUPPORT` are ordinary relations, not error states.
`MAY_SUPPORT` is the abstention edge: "this is the best candidate and it is not
good enough to assert."

A source location is a value a node carries, not a node of its own. That is
what keeps a locator attached to the thing it locates.

## Versioning

`aeg_schema_version` is graph-level; `schema_version` is per node. Additive
changes keep the major. Removing a field, or changing what an existing field
means, bumps it.

- An **unknown node or edge type** is kept and ignored. Dropping it would
  silently discard a plugin's evidence; erroring would let one bad plugin
  destroy the whole graph.
- An **unknown major** is refused with a structured error, never partially
  understood — the same rule the manifest loader follows.

## Ownership

A node is owned by `builtin` or by `plugin:<distribution>`. A producer may add
nodes and edges; it may not redefine another owner's node. Two different facts
under one identity from one owner is an error rather than a silent overwrite,
which makes double production detectable instead of last-writer-wins.

## On disk

Written under `.adduce/aeg/<digest>/` as `graph.json`, `nodes.jsonl` and
`edges.jsonl`, one entry per line, canonical JSON, through the same diff-gated
write boundary as every other artifact adduce produces. The directory is named
by the graph's own content id, so writing the same graph twice is a no-op
rather than an overwrite.

A repository controls `.adduce/`, so a stored graph is never trusted: every
entry is re-validated, every node is re-hashed against its recorded
`content_id`, and the set is checked against the header digest. A graph that
does not verify raises; the correct response is to rebuild, never to proceed
with whatever could be read.

## What is a node, and what is not

A call site is not a node. adduce indexes every call so that a rule can ask
about any qualified name, and almost none of that index is evidence about
anything: over the largest corpus repository, 7,657 of 386,146 call sites are
reachable through everything the 78 rules ask for — 1.98%, and 0.34% at the
medium stratum — while the most frequent names are `super`, `len` and
`isinstance`.

So the graph carries resolved operations, and the general call index stays in
the evidence layer where `ev.py.calls` can still answer for a name no producer
enumerated in advance. The graph is additive: it does not replace the index,
and its node count is an honest cost rather than a saving.

How a name was resolved decides what its fact is worth:

- a fully qualified call is `ast_resolved`
- a bare terminal match — `model.half()`, or `from_pretrained` on a receiver
  static analysis cannot type — is `lexical_match`, because the receiver is a
  guess

Neither may carry full confidence, which is correct: both are inferences about
what a name refers to.

## Status

The graph is being built producer by producer.

| producer | emits |
| --- | --- |
| configuration | `ConfigurationSnapshot`, `ConfigurationValue`, inferred `DependencyDeclaration` |
| python | `SourceFile`, `SeedOperation`, `ModelReference`, `DatasetReference`, `CheckpointReference`, `ExecutionCommand`, `EnvironmentConstraint` |

`Symbol` is deliberately not emitted yet: nothing reads it. Adding 39,397 nodes
at the largest stratum for no consumer is how the memory problem this project
already has was created in the first place, so it waits for claim retrieval,
which is what needs it.

Rules do not yet read from the graph — they still read the evidence
dataclasses, unchanged — so nothing in a check depends on it.
