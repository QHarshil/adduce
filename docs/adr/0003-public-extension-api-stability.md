# 3. Public extension API surface and stability

- **Status:** Accepted
- **Date:** 2026-08-24

## Context

adduce has two entry-point groups, not one:

- `adduce.rules` loads a module exposing `RULES`, an iterable of `Rule`
  subclasses.
- `adduce.reporters` loads a `CheckResult -> str` callable.

Both are real extension points, both are documented by example in
[extending.md](../extending.md), and neither carries a stability or deprecation
policy.

There is no stable namespace. `adduce/__init__.py` exports nothing but
`__version__`, so the effective public surface is whatever third parties import
from internal module paths.

Plugin loading itself is well hardened: per-entry-point isolation, deterministic
ordering, bounded sanitised diagnostics, no partial registration when iteration
fails, and a fallback to built-ins if discovery fails. But no test exercises an
out-of-tree plugin. Every plugin-loading test replaces the entry-point lookup
with a fake, so nothing goes through real `importlib.metadata` discovery of a
separately installed distribution. External projects now import these types and
test against new releases, which is the case a fake cannot cover.

The JSON report carries no schema or version key of any kind.

## Decision

Treat the plugin surface as public API, covering both groups.

Document a supported surface: `Rule`, `Finding`, `FindingItem`, `Status`,
`Category`, `Location`, `Evidence`, the repository view `applies_to` receives,
the reporter callable, both entry-point group names, and a deprecation policy.

Introduce a stable re-export namespace — a module with an explicit `__all__` and
no logic of its own — so that the thing we promise is the thing contract tests
import. Existing import paths keep working; this adds a surface rather than
moving one. Introduce it once the model it would export is settled, so the
namespace is not published twice with different contents.

Add a contract test that installs a genuinely separate distribution and
discovers it through real `importlib.metadata` entry points, exercising rule
loading, reporter loading, the public imports, and serialisation. The existing
fake-based tests stay: they cover failure isolation and ordering, which a real
fixture covers poorly.

Add an explicit schema version to the JSON report. Because there is no version
key today there is no compatibility debt to pay, and the version should land
together with the report's next structural change rather than modifying the
contract twice.

### Alternatives considered

**Documenting the internal module paths as the public surface.** Cheaper, and
rejected: it makes every internal reorganisation a breaking change, which is how
the surface came to be undefined in the first place.

**Relying on the existing fake-based plugin tests.** Rejected. Their own stated
rationale — that built-ins keep passing after a refactor that breaks external
imports — is exactly the gap they cannot close.

## Consequences

Existing external rules of the form `applies_to` plus `evaluate(ev) -> Finding`
must keep working without a rewrite, which constrains any child-result model to
be additive.

Committing to a namespace commits us to deprecation discipline. The policy has to
be written before the namespace ships, not after the first breakage.

The contract fixture is a packaging artifact and must not leak into the
distributed wheel or the coverage denominator.
