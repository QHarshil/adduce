# Documentation index

adduce is a local research-artifact auditor: an offline static audit, a
claims manifest, several report generators, and a small set of clearly fenced
online/execution layers. Start with the root
[README](../README.md) for install instructions and a worked example.

| Doc | Covers |
|---|---|
| [Concepts](concepts.md) | The three-layer framing (sharing vs. packaging vs. traceability), the manifest and claim trails |
| [Architecture](architecture.md) | The pipeline from scan to report, what each subsystem does, and the state each one is in |
| [Architecture decisions](adr/0000-index.md) | The settled design decisions, the context that forced each, and how a record is superseded |
| [Rule reference](rules/README.md) | All 78 rules across 17 categories, drift authority ranking, call resolution, and a page per rule |
| [Scoring](scoring.md) | Rule weights, category renormalisation, assessment coverage, tier thresholds, and the unrated floor |
| [CLI reference](cli-reference.md) | Installing and upgrading, every command, `reproduce`/`pin-remotes`, reviewer-time estimates, scoring/profiles/suppression |
| [Continuous integration](continuous-integration.md) | The composite GitHub Action, SARIF upload, pre-commit hook, baseline ratchet |
| [Evidence graph](aeg-schema.md) | The typed evidence IR: identity, provenance, resolution methods, versioning, and `adduce graph` |
| [Extending adduce](extending.md) | Writing a rule or reporter plugin via entry points |
| [Plugin API](plugin-api.md) | The supported surface of the `adduce.rules` and `adduce.reporters` entry-point groups, and its stability policy |
| [Generation safety](generation-safety.md) | The ten principles governing checklist, appendix, and manifest generation |
| [Optional LLM layer](llm.md) | BYO-key prose drafting; never determines a verdict |
| [Honest limits](honest-limits.md) | What adduce does not and cannot claim |
| [Security model](security-model.md) | Trust boundaries for every command, especially `--online`, `pin-remotes`, `reproduce`, and `--llm` |
| [Releasing](releasing.md) | Tagging and publishing a version |

Contributing, the validation corpus protocol, and the security policy live at
the repository root: [CONTRIBUTING.md](../CONTRIBUTING.md),
[corpus/README.md](../corpus/README.md), [SECURITY.md](../SECURITY.md).
