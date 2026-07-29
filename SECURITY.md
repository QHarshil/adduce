# Security policy

## Supported versions

Adduce is beta software. Security fixes are made on the active development
branch and released in the next stable version. Routine backports are not
provided.

| Release | Security support |
|---|---|
| Latest stable release on PyPI | Supported |
| Earlier stable releases | Not routinely supported |
| Development snapshots (`*.dev*`) | Best effort; not a supported release |

Users should upgrade to the latest stable release before reporting an issue
that may already have been corrected:

```console
python -m pip install --upgrade adduce
```

## Reporting a vulnerability

Please do not open a public issue for a vulnerability or include credentials,
private repository content, unpublished results, or personal data in a report.

1. If the repository's **Security** page offers **Report a vulnerability**, use
   [GitHub's private advisory form](https://github.com/QHarshil/adduce/security/advisories/new).
   Availability depends on the repository's current GitHub configuration.
2. If that form is unavailable, email `chudasama.h@northeastern.edu` with the
   subject `Adduce security report`.

Include the affected Adduce version, operating system, command or mode, a
minimal reproduction, the expected security boundary, the observed behavior,
and the likely impact. Redact all secrets and sensitive research data. If a
test repository is needed, use a minimal synthetic example that you are
authorized to share.

Test only repositories and systems you own or are authorized to assess. Stop
after demonstrating the issue, avoid disrupting third-party services, and do
not retain or disclose data that is not yours.

The maintainer will coordinate validation, remediation, and disclosure with
the reporter. No fixed response or release deadline is promised for this beta
project.

## Security boundaries

The default built-in static audit does not execute target repository code or
initiate remote-metadata requests. Several explicit features cross additional
trust boundaries:

- `adduce check --online` and `adduce pin-remotes --diff` make outbound HTTPS
  requests for repository-derived artifact identifiers.
- `adduce reproduce --yes` executes the selected repository command twice.
  Repository copying provides input isolation only; it is not a process,
  credential, filesystem, device, resource, or network sandbox.
- `adduce-rng-audit --yes ...` imports RNG libraries and
  executes the selected script unsandboxed in the Adduce process. Its warning
  and confirmation gate do not provide containment.
- Installed rule and reporter plugins are Python code and must be trusted as
  part of the Adduce environment.
- `adduce checklist --llm` sends the documented evidence summary to the
  provider explicitly configured by the user.

Likely-credential findings report a location and kind without the matched
value. This is a narrow finding-level control, not output sanitization or data
loss prevention. Generated drafts can include repository-derived commands,
paths, identifiers, and metadata. `.adduce/reproduce-report.json` records the
selected command and parsed numeric metric names and values verbatim. Do not
place credentials in repository commands or metric names, and inspect every
generated file before sharing it.

The complete boundary, implemented controls, residual risks, and recommended
operating practices are documented in the [security model](docs/security-model.md).

## Reports that belong elsewhere

False positives, false negatives, scoring disagreements, and documentation
errors are important but can normally be reported through the public issue
tracker when the report contains no sensitive information. A repository
command doing something harmful after the user explicitly starts
`adduce reproduce --yes` is not, by itself, a sandbox escape because that
command is not sandboxed. Report it privately if Adduce executed the command
without consent, exposed data contrary to the documented boundary, or allowed
the command to bypass an implemented containment control.
