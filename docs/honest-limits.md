# Honest limits

- **Signals, never certification.** adduce reports what it detected and what
  it could not; it never says "your code is reproducible", and it never
  assesses execution-based badges (Results Reproduced/Replicated).
- **Automatic claim inference is scaffolding.** Reliable claim trails
  currently require author-confirmed manifest claims; inferred
  repository-wide candidates may be missing or unrelated to the headline
  result and must not be treated as supported claims.
- **Static resolution has a ceiling.** Alias plus one-hop wrapper resolution
  handles the explicitly supported call shapes; coverage on unfamiliar ML
  repositories has not yet been established. Python's dynamism is not
  generally resolvable statically, so uncertain evidence is reported with
  confidence and can require a separately authorized dynamic check.
- **The probabilistic rules are diagnostic.** LaTeX numeric extraction,
  result reconciliation, notebook staleness, and ablation matching will
  sometimes miss or over-flag; they carry confidence and stay off the
  blocking path by default.
- **Remote pinning is a forward guarantee**, not recovery of the version
  historically used.
- **Dynamic reproduction is not a sandbox.** The copied workspaces separate
  run inputs, but the repository command retains the invoking user's host
  access and permissions.
- **Not a secret scrubber.** Likely-credential findings redact the matched
  value, but generated drafts can include repository-derived commands,
  paths, identifiers, and metadata, and reproduction reports retain the
  selected command and parsed metric names and values.
- **CUDA/cuDNN versions are rarely in source.** adduce checks whether
  anything *captures* them (container, conda env, manifest), not that it can
  read them from code.
- **Not a data-leakage detector.** Train/test contamination is undetectable
  statically and adduce claims nothing about it.
- **No project-operated backend.** Built-in checks run locally; only
  explicitly selected remote-metadata or provider features make network
  requests.

## Validation status

The [validation corpus protocol](../corpus/README.md) defines a pending
release-quality gate: a preregistered pilot running the analyzer against a
corpus of real repositories, compared against blind human review. No
effectiveness or calibration claim is made until its human-review
requirements are complete. See that document for the permitted conclusions
at any given point in the protocol.
