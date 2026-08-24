# 7. Collection is partly single-pass

- **Status:** Accepted
- **Date:** 2026-08-24

## Context

adduce has been described as collecting evidence in a single pass over
repository content. The shared pass is real: one scan feeds the Python,
remote-text and portability consumers, it is wired into evidence collection, and
a debug mode raises on a duplicate read so the property is checkable rather than
asserted.

But **3 of the 14 collectors sit behind it** — the three the code identifies as
"the collectors that all want source text". The other **11** each walk and read
independently, served by a 512-entry LRU cache. The shared-pass module's own
documentation argues that a bounded cache does not work at repository scale,
which is the reason the shared pass exists.

So the claim is true of part of collection and false of the whole.

## Decision

Preserve the shared-pass infrastructure and its telemetry.

Stop describing the whole collection system as single-pass. The accurate
statement is that a shared content pass exists and covers the three collectors
that want source text, while the remaining eleven read through a bounded cache.

Migrating the remaining eleven is deferred. It is a performance and architecture
improvement rather than a correctness fix, and eleven collectors is the size of
it.

### Alternatives considered

**Migrating all eleven now.** Rejected as sequencing, not as direction: it
touches every collector, and the benefit is throughput on large repositories
rather than any change in what adduce reports.

**Removing the shared pass and relying on the cache.** Rejected. The shared pass
measurably removed repeated decoding of every Python file, and the cache is
bounded precisely where repositories are largest.

## Consequences

Telemetry already separates shared reads from disk reads and cache hits, so real
coverage is measurable at any time and a future migration has before-and-after
numbers without new instrumentation.

The benchmark's read-amplification metric is the instrument that will show
progress, and it is already gated against a committed baseline.

An honest partial claim serves a reader better than an aspirational total one,
and costs nothing: the performance benefit that exists is real either way.
