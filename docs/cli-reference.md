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
adduce check --gitignore             # skip paths git ignores (off by default; see below)
adduce check --timings               # per-stage durations and work counters, to stderr
adduce drift                         # paper ↔ code/config consistency + result reconciliation
adduce precision                     # TF32/AMP/low-precision audit
adduce deps                          # ghost/unused/notebook dependency analysis
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

## Scan scope and `--gitignore`

By default the scan walks every file except a fixed list of build and
environment directories. It does **not** consult `.gitignore`, so a gitignored
`data/`, `wandb/`, `outputs/`, or vendored checkout is inventoried, read, and
allowed to contribute findings — including passing ones — even though it is not
part of the artifact a reader receives.

`--gitignore` drops those paths, using git's own matcher so nested ignore files,
negation, and anchoring behave exactly as git does. Two consequences worth
knowing before enabling it:

- **It can lower a score, and that is usually the point.** A repository that
  vendors another project can earn passing statuses from the vendored code. On
  this repository, enabling it moves 30 rule statuses: nine rules stop applying
  at all, twelve become not-applicable, and nine that were passing or partial
  drop — because their evidence came from files belonging to other repositories.
- **Tracking beats ignoring**, as in git: a tracked file matching an ignore
  pattern is still scanned.

Scanning a directory that is itself gitignored keeps every file, since
filtering there would report a clean repository containing nothing. When git is
unavailable or the directory is not a repository, the flag has no effect: the
scan includes more, never silently less.

It is off by default while its effect on findings is under review, and is
planned to become the default in a later release.

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
```

Suppressed findings still appear, marked as ignored, and retain their
observed score. Suppression records an accepted exception; it does not count
missing evidence as a pass. Repository-configured exclusions reduce scan
scope and are disclosed in reports. `--mode reviewer` and `--mode ae-chair`
bypass repository-supplied profile, ignore, exclude, and threshold settings
unless the caller supplies explicit CLI options.
