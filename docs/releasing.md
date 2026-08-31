# Release process

Adduce releases are built from an annotated stable-version tag on `main`.
Creating a tag is a release decision: the `release.yml` workflow validates,
builds, and publishes that exact commit to PyPI after approval in the protected
`pypi` environment. A GitHub Release is optional and independent of this
workflow.

## One-time repository configuration

1. Create a GitHub environment named `pypi`, limit it to protected stable tags,
   and require an authorized maintainer to approve deployments.
2. Keep the `v*` tag namespace immutable. The ruleset that does this blocks
   deletion and non-fast-forward updates on `refs/tags/v*`; it restricts nobody
   from creating one, so what stands between a pushed tag and a published
   release is the `pypi` environment approval above.
3. In the existing PyPI `adduce` project, register a GitHub Trusted Publisher
   for repository `QHarshil/adduce`, workflow `release.yml`, and environment
   `pypi`.

No long-lived PyPI token belongs in GitHub secrets. The publish job receives
only `id-token: write`; it downloads the distributions produced by the
unprivileged build job and passes them to the pinned PyPA publishing action.

## Release gates

Before proposing a tag:

- complete the version's corpus and human-review gates, or document explicitly
  which validation remains developmental;
- update `pyproject.toml` and `src/adduce/__init__.py` to the stable version;
- move the relevant changelog entries into a dated version section;
- update `CITATION.cff`, the README current-release line, and the composite
  Action's default package version;
- run the full test, type, coverage, build, metadata, and clean-install gates;
- merge the exact release candidate to `main` and verify required checks.

Run the local metadata gate before tagging:

```bash
python scripts/validate_release.py --tag vX.Y.Z
```

The tag workflow installs its gate tools through `release-constraints.txt`
rather than resolving them at tag time, so `ruff`, `mypy` and `pytest` answer on
the tag the way they answered in CI on the same commit. Bump that file
deliberately, in a pull request, with CI green on the new set.

Create and push an annotated tag only after the release commit and environment
approval are ready. The tag workflow requires the pushed ref to be an annotated
tag whose commit is an ancestor of `origin/main`, reruns lint, type checking,
the coverage gate, the Markdown-link and reviewer-material checks, the metadata
gate and the packaging and clean-install checks, transfers only the validated
wheel and source archive to the publish job, and publishes through PyPI Trusted
Publishing with digital attestations.

It does not rerun all of CI. Those gates run on ubuntu with Python 3.12 only:
the 3.10–3.14 matrix, macOS, Windows, the lowest-direct-dependency job, the
external plugin contract, the benchmark regression gate and the self-check run
on the pull request and on `main`, not on the tag. Confirm them green on the
release commit before tagging, because a tag cannot be moved.

If any gate fails, correct the source through the normal review process and use
a new version. Do not move or replace a published release tag.
