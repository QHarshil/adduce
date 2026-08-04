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
