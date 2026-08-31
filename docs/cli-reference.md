# CLI reference

## Installing and upgrading

```console
pipx install adduce        # or: pip install adduce / uvx adduce
adduce check .
```

Existing installations do not update automatically. Upgrade with the command
for the installer you used:

```console
python -m pip install --upgrade adduce
pipx upgrade adduce
uv tool upgrade adduce
```

For a one-off run that explicitly selects the latest release, use
`uvx adduce@latest --version`.

## Commands

```bash
adduce check .                       # everything offline: report, claim trails, reviewer time
adduce check --mode reviewer         # skeptical framing: what could not be verified
adduce check --mode ae-chair         # badge prerequisites, blocking issues, burden headline
adduce check -f json|sarif|markdown|badge|latex -o out
adduce check ./code --paper ../paper       # paper and code kept in separate repositories
adduce check --no-gitignore          # scan the whole tree, ignore file included (see below)
adduce check --timings               # per-stage durations and work counters, to stderr
adduce drift                         # paper ↔ code/config consistency + result reconciliation
adduce precision                     # TF32/AMP/low-precision audit
adduce deps                          # ghost/unused/notebook dependency analysis
adduce graph --format json           # artifact evidence graph: nodes, edges, provenance
adduce manifest                      # scaffold .adduce/manifest.yaml
adduce manifest --refresh            # write a separate refresh proposal; never overwrite author content
adduce checklist --profile neurips   # repository-evidence checklist draft (also: acl); --strict-evidence
adduce appendix                      # ACM Artifact Appendix draft; --strict-evidence
adduce package --profile neurips     # one-command submission bundle (checklist, appendix,
                                     # manifest, ledger, checksums, RO-Crate) in adduce-submission/
adduce audit-generated checklist.md  # audit a generated artifact against its evidence ledger
adduce export ro-crate|croissant|codemeta|zenodo|checksums|software-heritage|all
adduce badge --svg                   # committed-in-repo badge; no hosted endpoint
adduce diff main...HEAD              # artifact regression: code changed, docs/manifest did not?
adduce archive-plan                  # exact steps to a Zenodo DOI / Software Heritage SWHID
adduce baseline                      # snapshot for the CI ratchet
adduce rules · adduce explain R-DET-001
adduce fix --scaffold seeds|docker|citation|runner|readme

# opt-in, clearly fenced:
adduce pin-remotes --diff            # resolve current Hugging Face revisions (online), show pin diffs
adduce reproduce --yes               # run the smoke target twice, assert the runs agree (executes repo code)
```

`adduce reproduce` is the empirical layer: two runs with the same declared
seed environment, fingerprinted (output hashes and stdout metrics), and
compared. The target command must actually consume `ADDUCE_SEED` or apply its
own equivalent seeding; Adduce does not inject framework seed calls. It
executes repository code, so it demands `--yes`, is designed to run inside a
disposable container or virtual machine, and is never invoked by `check`.
Repository copying provides input isolation only; it does not provide
process, credential, filesystem, device, resource, or network isolation.
Adduce bounds captured streams and expected-output hashing, but those
safeguards do not limit what the command can access or consume. The
reproduction report records the selected command and parsed numeric metric
names and values verbatim, although it does not retain the captured streams;
do not put credentials in commands or metric names, and review the report
before sharing it. The best-effort first-use diagnostic
(`adduce-rng-audit --yes train.py`) also imports libraries and executes the
selected script without a sandbox; it reports ordering only for supported
module-level Python, NumPy, and Torch RNG calls and does not observe
generator-instance methods or library-internal/native draws. Read the
[security model](security-model.md) before either execution mode.

`adduce graph` inspects the artifact evidence graph built from the same
evidence a check run collects; it is diagnostic only and changes nothing
about a check. See [Evidence graph](aeg-schema.md) for the node and edge
schema.

## Output formats and item detail

A `Finding` may carry structured child observations (`FindingItem`) explaining
its verdict — see [Finding items](plugin-api.md#finding-items). Formats split
into two groups over how they carry those children:

- **`json` and `sarif` are machine-readable and carry every item.** `json`
  carries every item of every finding without exception. `sarif` carries
  every item of every finding it reports, and it reports only actionable
  findings — `FAIL` and `PARTIAL` — so a `PASS`, `UNKNOWN` or `NOT_APPLICABLE`
  finding's items never reach SARIF either, along with the finding itself.
- **`markdown`, `latex`, and `adduce check --verbose` are human-readable and
  summarise.** Instead of listing children, a finding that carries items adds
  one line: `"<n> item(s) not listed here: <split>"`, where `<split>` is a
  count per status (for example `"1 partial, 3 fail"`). No child's content
  appears in these formats. `markdown` and `latex` show this line for every
  finding that carries items; terminal output shows it only under
  `--verbose` — at default verbosity the terminal report carries no item
  detail at all.

## Scan scope and `--no-gitignore`

The scan skips paths git ignores, using git's own matcher so nested ignore
files, negation, anchoring, and directory-only patterns behave exactly as git
does. A gitignored `data/`, `wandb/`, `outputs/`, or vendored checkout is not
part of the artifact a reader receives, so it is not evidence about that
artifact.

`--no-gitignore` examines the whole tree instead. Three consequences worth
knowing:

- **Honouring the ignore file can lower a score, and that is the point.** A
  repository that vendors another project, or keeps working clones under an
  ignored path, can otherwise earn passing statuses from files that are not part
  of the artifact a reader receives — cuDNN determinism flags detected in a
  project that contains no CUDA code of its own, for one. Scanning the whole
  tree instead moves rule statuses in both directions: rules stop applying, turn
  not-applicable, drop a status, or improve one.
- **Tracking beats ignoring**, as in git: a tracked file matching an ignore
  pattern is still scanned.
- **It never scans less than it reports.** Scanning a directory that is itself
  gitignored keeps every file, since filtering there would report a clean
  repository containing nothing. When git is unavailable, the call fails, or the
  directory is not a repository, the whole tree is scanned.

Ambient git configuration cannot change the answer: system and global config
are suppressed for the query, so a user's `core.excludesFile` cannot silently
shrink an audit.

`bench/runner.py finding-diff` enumerates every rule status that honouring the
ignore file moves on a given tree. Run it rather than quoting a figure from
here: what moves depends on what the working tree happens to carry, which is
not a property of the release.

## Scores, tiers, and when no tier is given

The score is a category-weighted average over the rules that applied. Rules that
do not apply are excluded in both directions, so a scikit-learn project is never
scored against CUDA determinism.

That exclusion has a consequence worth stating plainly: **most rules are
assertions about code, so a repository with very little code has very little to
be wrong about.** Rules that look for a problem find none and pass. Rules that
look for an artifact are satisfied by its presence. A directory of
plausible-looking but meaningless files — a README with the right headings,
pinned requirements nothing imports, a config no code reads — measured
72/100 and "Silver" on ten lines of source, and an empty directory measured 47.

So a tier is assigned only when at least 100 physical lines of source were
actually parsed. Below that the tier reads:

```
Reproducibility  72/100   Unrated (insufficient evidence)
```

The score is still shown, because it is a real measurement of the checks that
ran. What it is a score *of* is what the tier cannot vouch for.

`--format json` carries the same information in an `evidence_base` block. This
one is measured on a fixture in this repository, so you can reproduce it:

```console
adduce check corpus/synthetic/synthetic_tf32 --format json
```

```json
"evidence_base": {
  "rated": false,
  "evaluated_rules": 37,
  "considered_rules": 69,
  "applicable_rules": 38,
  "coverage_percent": 97.4,
  "analysable_lines": 4,
  "rules": {
    "assessed": 37,
    "unknown": 1,
    "not_applicable": 31,
    "skipped_inapplicable": 9
  }
}
```

`coverage_percent` is `evaluated_rules / applicable_rules`, not
`evaluated_rules / considered_rules`: a check that does not apply to the
repository never had an answer to reach, so counting it against coverage would
understate what was assessed. The difference is large here, not cosmetic —
37/38 reads 97.4 %, and 37 over the 69 rules considered would read 53.6 % of a
repository that left only one question open. The `rules` block names three of
the [five outcomes](scoring.md#five-outcomes-for-a-registered-rule) a registered
rule can reach — assessed, unassessed, and evaluated-not-applicable — plus one
of the two causes of a fourth, so the nine rules `applies_to` excluded here stay
visible without entering that fraction. It carries nothing for a rule the
profile disabled or one that could not supply its identity; those reach
telemetry only. [Scoring](scoring.md#coverage) sets out the arithmetic.

**Provenance of the `evidence_base` block above, and of the two percentages
computed from it.** Measured 2026-08-31 against analyzer source tree
`9490666122b9a271113b062d8f1d4f0443fe3af79cd2f9ce6eae44709af1a468`, on CPython
3.14.0, darwin arm64, by the command shown, under the default profile with no
rule plugins installed. The fixture is version-controlled here, so you can run
the command and compare; the counts still move when a rule is added or a rule's
applicability changes, which is why one block dates them together rather than
each in turn. The digest, not a commit hash, identifies what was measured:
writing a commit hash into the commit that carries it changes it. Regenerate it
with `python3 corpus/scripts/review_facts.py show --root .`. A later tree that
reports different counts does not make the block stale — it stays a true
statement about the tree it names.

Two limits, both deliberate:

- **This is not a defence against deliberate gaming.** Padding a file with a
  hundred lines defeats it. It exists so an audit does not present a verdict on a
  repository that has not shown enough to support one.
- **A repository adduce cannot read is unrated, not bad.** A project written
  entirely in R or MATLAB parses to zero Python lines and receives no tier. That
  is an honest statement about the analyzer's reach, not a judgement about the
  work.

## Timings

`--timings` reports per-stage durations and work counters on stderr, and adds a
`telemetry` block to `--format json`. Durations differ between identical runs,
so they are absent unless requested and the default report stays byte-stable.
The counters include `files.read_from_disk` against `files.inventoried`, which
is how much file decoding a run repeats.

`adduce pin-remotes` resolves current revisions and drafts
`revision="<sha>"` edits as diffs (libcst codemods, applied only with
`--write`). Pinning to the *current* SHA is a forward guarantee — it does not
recover the version historically used, and the output says so.

## Reviewer time to first result

The reviewer-time estimate uses four buckets: `< 10 min` Excellent · `10–30`
Good · `30–90` Risky · `90+` High reviewer burden. It lists the contributing
signals (for example, no one-command path, manual data fetch, no smoke
target, or undocumented runtime) so the estimate remains inspectable.

## Scoring, profiles, suppression

Scoring is category-weighted and explainable — each category reports
earned/possible with the findings that moved it; inapplicable categories drop
out and the rest renormalise, so a scikit-learn repository is never scored
against CUDA flags. Profiles: `default`, `neurips`, `iclr`, `acl`, `acm`,
`strict`, or your own TOML.

Scores and named tiers are experimental prioritisation aids. They are not
calibrated quality grades and should not be used to rank unrelated
repositories; repositories can have materially different applicable-rule
denominators.

Every finding carries four separate dimensions — status, confidence,
severity, and score weight — because a low-confidence high-severity issue (a
possible committed secret) must not read the same as a high-confidence
low-severity one (a missing `.zenodo.json`).

```python
loader = DataLoader(ds, shuffle=True)  # adduce: ignore=R-DET-004
```

```toml
[tool.adduce]           # or adduce.toml
profile = "neurips"
ignore = ["R-ARC-001"]
exclude = ["third_party"]
fail-under = 70          # or fail_under; not both
```

Suppressed findings still appear, marked as ignored, and retain their
observed score. Suppression records an accepted exception; it does not count
missing evidence as a pass. Repository-configured exclusions reduce scan
scope and are disclosed in reports. `fail-under` (or `fail_under`, never
both) sets the same gate as `--fail-under`, a finite number from 0 to 100; an
explicit `--fail-under` on the command line takes precedence over the
repository's configured value. `--mode reviewer` and `--mode ae-chair`
bypass repository-supplied profile, ignore, exclude, and threshold settings
unless the caller supplies explicit CLI options.
