# Security model

This document defines the security boundaries of Adduce's local static audit,
opt-in network resolution, generated artifacts, extension points, and dynamic
reproduction. It describes the `0.1.x` beta line; release notes identify
material changes.

Security vulnerabilities should be reported according to
[SECURITY.md](../SECURITY.md), not through a public issue.

## Assets and trust assumptions

Adduce may be run against an untrusted research repository. Repository files,
manifests, URLs, remote responses, generated output paths, and reproduction
commands must therefore be treated as untrusted input.

The following components remain trusted:

- the host operating system, Python interpreter, certificate store, DNS
  resolver, and installed Adduce distribution;
- installed Python dependencies and Adduce rule or reporter plugins;
- user-supplied command-line arguments and configuration outside the audited
  repository;
- any external model provider explicitly selected for `checklist --llm`.

The assets at risk include source repositories, unpublished research data,
credentials and tokens in the process environment or home directory, other
host files accessible to the user, network services reachable from the host,
and the integrity of generated reports.

## Operation boundaries

| Operation | Network | Executes code | Writes |
|---|---|---|---|
| Built-in `adduce check` | No | Does not execute target code; invokes local Git for metadata | Only an explicitly selected report path |
| `adduce check --online` | Public HTTPS metadata | Same as `check` | Selected report path and `.adduce/cache` |
| `adduce pin-remotes --diff` | Public HTTPS metadata | No target code | `.adduce/cache`; proposed edits go to the terminal |
| `adduce pin-remotes --write` | Public HTTPS metadata | No target code | Cache and explicitly previewed source edits |
| `adduce reproduce --yes` | Whatever the repository command can reach | The selected command, twice | Temporary workspaces and `.adduce/reproduce-report.json`; the command retains host access |
| `adduce-rng-audit --yes ...` | Whatever the target can reach | Imports RNG libraries and executes the selected script | Whatever the target can write; diagnostics go to standard error |
| `adduce checklist --llm` | Configured provider or local endpoint | Provider client code | Requested unverified draft and evidence ledger with non-secret provider/model provenance |

The standard CLI discovers installed plugins. Installed plugins are imported
Python code, execute in the Adduce process, and can make their own network and
filesystem calls. The table describes built-in behavior, not plugin behavior.
Use a clean environment containing no third-party Adduce plugins when auditing
an untrusted repository.

## Default static audit

The built-in offline pipeline inventories regular, non-symlinked files, reads
selected content as data, parses supported formats, and invokes read-only Git
commands for repository metadata. It does not import or launch Python files
from the target repository. `adduce check` does not call the online resolver or
the dynamic runner unless the user selects a separate opt-in mode.

Static parsing is not a malware analysis or denial-of-service boundary. Very
large or adversarial files and directory trees can consume time, memory, and
disk resources. Run the audit as an unprivileged user and apply operating-system
resource limits when the repository is not trusted.

## Opt-in online resolution

Online resolution sends repository-derived artifact identifiers from the
user's machine. Hugging Face and GitHub identifiers are sent to their public
APIs; raw remote URLs are contacted directly. Adduce does not upload source
files, but the detected identifier itself is transmitted and a raw URL's path
or query can originate verbatim in repository source. Destination services and
the local network can also observe normal connection metadata.

URL query strings are transmitted to the selected destination even though
their values are redacted from diagnostics. Inspect detected references before
enabling online resolution, and do not resolve a URL containing a credential or
other value the destination is not authorized to receive.

The resolver implements the following controls:

- only HTTPS on the default HTTPS port is accepted;
- embedded credentials, control characters, backslashes, zone-scoped or
  percent-encoded hosts, single-label names, and known local-name suffixes are
  rejected;
- complete raw URLs are retained for resolution rather than silently truncated,
  and URLs longer than 8,192 UTF-8 bytes are rejected before DNS or transport;
- literal and DNS-resolved destinations must all be globally reachable IP
  addresses; loopback, private, link-local, and other non-global addresses are
  rejected, as are multicast, unspecified, reserved, and IPv6 site-local
  addresses; at most 16 fully validated addresses are attempted;
- the connection is pinned to the validated address set and the connected peer
  is checked again, while TLS certificate and hostname validation remain
  enabled;
- every redirect destination is independently validated, and at most three
  redirects are followed;
- application-supplied request headers are limited to fixed `Accept`,
  `Connection`, and `User-Agent` values; environment proxy settings, cookies,
  and authorization headers are not used;
- after initial synchronous DNS validation, TCP connection, TLS handshake,
  request transmission, response headers, redirects, and response bodies share
  an absolute 30-second deadline; a deadline timer closes the active connection
  and socket so a peer cannot extend it by slowly dripping bytes;
- JSON metadata bodies are limited to 1 MiB, must be JSON objects, and commit
  identifiers must be full 40-character hexadecimal SHAs;
- only terminal 2xx responses establish availability; supported redirects are
  revalidated, and other 3xx responses remain unsuccessful;
- diagnostic URLs remove credentials and fragments and replace raw paths and
  query values with redaction markers; response header values are normalized
  and truncated before display.

Raw URL checks use `HEAD` and do not download a response body. API lookups use
bounded `GET` responses.

The operating system's synchronous DNS resolver cannot be interrupted portably.
Initial DNS therefore occurs before the enforceable transport deadline, and a
redirect lookup can overrun the remaining budget before Adduce observes that it
has expired. Use an external wall-clock limit when auditing adversarial input or
when DNS availability is uncertain.

### Resolution records

Successful and failed resolution metadata is written under `.adduce/cache` for
local inspection. Repository contents are untrusted, so a new resolver session
never accepts a pre-existing entry as network evidence. Only entries written by
the current resolver session can be read back during that session.

Keys use SHA-256 digests rather than repository-derived filenames. Entries
carry a schema version, key digest, and timestamp, are limited to 1 MiB, and
are written through a same-directory temporary file followed by an atomic
replace. Symlinked cache directories and entries are rejected. Cache I/O or
validation failures are ignored rather than changing a live resolution result.
The files are inspectable records, not reusable authenticated evidence and not
a substitute for a fresh network resolution.

Recorded metadata does not establish which remote revision produced a
historical result. A current commit SHA is a forward pinning aid, not provenance
for an earlier experiment. Network failures remain unknown rather than proving
that an artifact has disappeared.

These resolver controls apply only to Adduce's online resolver. They do not
constrain network access by installed plugins, configured model providers, or
commands launched through `adduce reproduce`.

## Dynamic reproduction is not a sandbox

`adduce reproduce --yes` runs a shell command selected by the user or declared
in the repository manifest. It makes two temporary copies so each attempt
starts from separate repository inputs and removes copied expected outputs
before execution. Repository symlinks are omitted from those copies.

Copying provides input isolation only. The launched command still:

- runs with the invoking user's operating-system identity and permissions;
- inherits the host environment, including any credentials present there;
- can access host files, devices, processes, and services available to that
  user;
- has host network access unless an external runtime removes it;
- is interpreted by the platform shell;
- may create child processes and consume CPU, memory, process, file, and disk
  resources.

The temporary copy does not protect the original repository or any other host
path from a command that deliberately locates and modifies it. `--yes` records
consent to execution; it does not make the command safe.

Adduce bounds the evidence it retains and hashes:

- the retained tail of standard output and standard error is limited to 1 MiB
  per stream, per run, and the report records whether either stream was
  truncated;
- an expected output must be a regular, non-symlinked file reached through
  non-symlinked directories inside the run workspace, and each expected output
  is limited to 512 MiB for hashing;
- each run requires an integer timeout from 1 minute through 24 hours; on POSIX
  systems a timed-out command's isolated process group is terminated, while
  non-POSIX descendants still require external containment;
- `.adduce/reproduce-report.json` is written by atomic replacement, and Adduce
  refuses a detected symbolic-link or non-regular report destination.

These are fingerprinting and report-write safeguards, not command resource
limits. A command can consume resources or write large and unrelated files
before Adduce rejects an expected output. The report stores the command string
and extracted numeric metric names and values verbatim, although it does not
store the raw captured streams. Credentials and sensitive values must not be
embedded in the command or emitted as metrics.

For untrusted repositories, run reproduction in a disposable, unprivileged
container or virtual machine with no credentials, SSH agent, cloud metadata
access, Docker socket, or sensitive mounts. Disable or restrict network access,
mount external data read-only, and enforce CPU, memory, process-count, file-size,
disk, and wall-clock limits outside Adduce. Inspect the command and its inputs
before execution.

### First-use RNG diagnostic

`adduce-rng-audit --yes <script.py> [args...]` is another
dynamic execution mode, not a static inspection. It imports supported RNG
libraries from the active Python environment and executes the selected script
as `__main__` in the Adduce process. Use the installed console entry point as
shown: a raw `python -m adduce...` command started inside an untrusted repository
can resolve a repository-local parent package before Adduce's consent check.
The target can also shadow RNG modules imported after consent, and it retains
the current user's environment and host filesystem, process, device, and
network access.

The module always prints this boundary before doing any target-related import
or execution and refuses to continue unless `--yes` appears in the documented
position. Confirmation records intent; it provides no sandbox. Apply the same
disposable, unprivileged external containment required for reproduction. The
hook is best-effort evidence about supported module-level calls only and does
not observe generator-instance methods, native/library-internal draws, worker
subprocesses, deliberate wrapper removal, or process exits that bypass Python.

## Generated artifacts and provider use

Generated checklists, appendices, manifests, and metadata are drafts. Their
evidence ledger preserves source locations, confidence, evidence strength, and
generation provenance; it does not turn static evidence into an execution
claim. Authors must review generated content before submission. The separate
[generation-safety contract](generation-safety.md) defines these rules.

Secret detection is heuristic, not a complete secret scanner. When Adduce
detects a likely credential, the finding and its evidence entry record the
location and kind rather than the matched value. This finding-level redaction
does not sanitize other output. Generated drafts can reproduce
repository-derived commands, paths, identifiers, and metadata, and dynamic
reports retain the selected command and parsed numeric metric names and values
as described above. Do not rely on Adduce to make an untrusted repository safe
to publish; inspect every generated file before sharing it.

The optional provider layer is disabled unless explicitly configured. It sends
the selected checklist question and deterministic finding summaries, which can
include repository paths, artifact identifiers, and detected metric or
configuration values. It is governed by the configured provider's security,
privacy, and retention terms. Do not enable it for content that the provider is
not authorized to receive. Repository-derived summaries are untrusted input and
can contain adversarial language; provider output is therefore labelled
unverified, never counts as evidence, carries provider/model and fragment-hash
provenance, and requires author review. Provider responses are bounded to
1 MiB.

## Extension and supply-chain risk

Rule and reporter entry points are executable Python extensions, not declarative
rule files. Error isolation prevents one broken plugin from stopping discovery,
but it does not confine a malicious plugin. Install extensions only from trusted
publishers and audit them under the same policy as any dependency.

Pin the Adduce version in automation, review dependency changes, and install in
an isolated environment. For higher-assurance automation, pin GitHub Actions to
a reviewed full commit SHA and verify package hashes. A Git tag, checksum, or
successful Adduce report is evidence about a particular artifact; none alone
establishes that its dependencies or plugins are benign.

## Security non-goals

Adduce does not claim to:

- sandbox, contain, or attest repository commands;
- detect malware, every secret, data poisoning, or train/test contamination;
- prove that a repository is reproducible, safe, or suitable for publication;
- recover the historical identity of a floating remote artifact;
- make third-party plugins, dependencies, model providers, or remote services
  trustworthy;
- prevent resource exhaustion by an adversarial repository on an unrestricted
  host.
