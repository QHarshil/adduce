# Benchmark harness

Measures what an audit costs and what it finds, so a change to either is visible
rather than inferred. Before this existed there was no timing instrumentation
anywhere in `src/adduce`, and the only performance evidence was whole-process
wall clock and peak memory recorded by the validation harness.

## Why this lives outside `corpus/`

The preregistration analysis plan is an explicit tuple of paths
(`corpus/scripts/preregistration.py`), not a glob. A harness placed here changes
no frozen digest, so it can evolve while human review is in progress. It may
import from the hashed corpus tooling; nothing hashed may import from it.

## Rules

- **Nothing is estimated.** A target that is absent, or a measurement that
  fails, is recorded with the reason. A report is still valid without it.
- **A quantity that cannot be measured on this platform is reported absent**,
  not defaulted to zero. `ru_maxrss` is bytes on Darwin, kibibytes on Linux, and
  unavailable on Windows, so the unit travels with the value and the report says
  which platform produced it.
- **Peak memory is measured one target per process, and one analysis per
  process.** Peak RSS is a process high-water mark, so measuring several
  targets in one interpreter would report the largest of them for all of them,
  and measuring two analyses in one process would report their combined peak
  for one. Measured on `transformers`: 353 MB for one `run_check` call, versus
  ~595 MB when a worker ran two and kept the first `CheckResult` alive for a
  byte-identity comparison against the second. No user runs an audit twice and
  keeps both results resident, so `measure`'s performance worker runs exactly
  one `run_check`; determinism is checked by a separate worker invocation that
  runs two and reports nothing else.
- **Performance is measured across independent processes, once per rep**
  (`--reps`, default 3 for `measure` and 5 for `ab`), never several reps sharing
  one process. Within-arm spread under the previous two-run-per-process worker,
  at ~600 MB, reached **13%**: identical code measured 17.844 s, 19.970 s, and
  20.188 s across three reps. A report's `cold_runtime_seconds` is the median of
  the reps, `cold_runtime_samples` is the list, and `cold_runtime_spread` is
  `(max - min) / median` — the harness's own measured noise floor, not an
  estimate of it.
- **A repeat run is not a warm run.** There is no analyzer cache yet, so
  `repeat_runtime_seconds` differs from cold only by operating-system page
  cache. It lives in the determinism record, which carries
  `warm_path_exists: false` until that changes.
- **Size strata are assigned from measured Python LOC**, never declared in the
  manifest.

## Any runtime claim needs `ab`, not a stored baseline

`compare` checks one fresh sample against the committed baseline. That
comparison is not paired — the baseline and the new sample were never measured
under the same machine state — and it cannot resolve an effect anywhere near
the size a real change in this analyzer typically produces. Measured on this
machine: comparing one stored sample of `transformers` against one new sample
reported cold runtime **+13.9%**, for a change whose true paired effect,
measured correctly, was **-11.3%**. The single-sample comparison got the sign
wrong, not just the magnitude.

`bench/runner.py ab` is the tool for an actual runtime claim. For each target it
runs the two source trees back to back within every rep, and flips which one
leads on each rep, so both arms see the same machine state and neither arm
carries the cost of going first:

```console
python bench/runner.py ab --baseline-src /path/to/old/src --current-src src --reps 5
```

**The comparison is paired, so it is read pairwise.** A delta counts as a result
only when *every rep moved the same way* — a sign test, a 2^(1-n) coincidence
under the null, 6% at the default five reps. It assumes no distribution, which
is the right bar for a handful of wall-clock samples.

Comparing the two arms' aggregate spreads instead does not work, and the reason
is worth stating: on a loaded machine the drift *underneath* both arms is
routinely larger than the effect *between* them. One real measurement of
`torchtune` had every one of six reps faster, by 3.8% to 8.8%, while each arm's
own spread was 6-8% — an aggregate-spread test abstains there, and it is wrong
to. The pairing already cancels the drift; the statistic must not pay for it
twice.

A delta whose reps disagree in sign is printed as **not resolvable**, never as a
result. A single sample against `baseline.json` in CI is a coarse tripwire for
gross regressions, not evidence for a specific percentage, and a `compare` delta
must never be quoted as a measured effect size.

Run `ab` with the same tree in both arms to see the floor: it should report not
resolvable. If it does not, the machine is too noisy to measure on right now.

## Running it

```console
python bench/runner.py measure --output bench/reports/local.json
python bench/runner.py measure --output bench/reports/one.json --only torchtune
python bench/runner.py measure --output bench/reports/one.json --only torchtune --reps 5
python bench/runner.py compare --baseline bench/reports/baseline.json \
                               --current bench/reports/local.json
python bench/runner.py finding-diff --only adduce-self
python bench/runner.py ab --baseline-src /path/to/old/src --current-src src \
                          --only torchtune --reps 5
python bench/finding_items.py --output bench/reports/finding-items.json
```

`finding_items.py` is the second entry point here and it measures something
different: what a finding's child results cost to construct, serialise and
render, at 10,000, 50,000 and 100,000 items. It scans no repository, reads no
target from `strata.json`, and stays **out of the CI regression gate**, which
compares repository scans against a stored baseline. `runner.py` answers "did a
scan get slower"; this answers "what does a child result cost". Every size is
measured in its own process, and peak RSS is reported apart from the traced
allocation figures because the two count different things.

`compare` exits non-zero when, against the baseline, a repeated run stops being
byte-identical, parser failures rise, a synthetic-corpus score moves, or disk
reads per inventoried file rise. **Those four gates are exact** — they compare
quantities that do not depend on the machine — and they are the ones worth
trusting.

**`compare` does not gate cold runtime**, beyond calling a gross failure past
`4x`. Wall clock across two *separate* reports cannot be made precise, and the
reps do not rescue it: reps measure the spread *within* one report, which says
nothing about the drift *between* two. `transformers` was measured here at
**14.5 s and at 39.6 s** hours apart on one laptop — 2.7x — while the three reps
inside the slower report agreed to **6.1%**. A gate scaled to that 6.1% would
fire constantly; one scaled to the 2.7x detects nothing real. So a runtime claim
comes from `ab`, which pairs the arms and cancels that drift by construction,
and never from a `compare` delta.

For the same reason, **`baseline.json`'s runtime figures are context, not a
contract.** The provenance block records the load average they were taken under
so a reader can see it. Regenerate them on an idle host if you want them to mean
anything in absolute terms.

`finding-diff` runs each target with the ignore file honoured and ignored, and
enumerates every rule status that moves between the two. Each move is classified
as a rule that stopped applying, became not-applicable, dropped, or improved —
producing no finding at all is a different fact from reaching a new conclusion,
so the two are never merged. This is the evidence for honouring `.gitignore` by
default, and on the adduce repository it reports 30 moves: 9 / 13 / 7 / 1 in that
order.

Both arms of `measure` pass the ignore setting explicitly, so a change to the
shipped default cannot silently move a measurement or a baseline comparison.
`default` honours `.gitignore`, matching `adduce check` with no arguments; the
six targets declaring `measure_gitignore_delta` additionally run a `whole_tree`
arm with `--no-gitignore`, so the cost and effect of that default stays visible.
`finding-diff`'s enumeration above reads the same direction: `whole_tree`
(before) to the honoured tree (after).

Clone targets live under `corpus/clones/`, which is gitignored and ships in no
archive. They are reported unavailable in CI; the synthetic targets always run.

## Known gaps in the target set

- **No target in the L stratum** (150k–1M Python LOC). Recruiting one is
  outstanding.
- `corpus/repos.csv` records `has_tex=false` for all 15 rows, so claim
  extraction from LaTeX is exercised only against synthetic papers.

## Instrumentation overhead

Measured on the largest target (`transformers`, 4,643 Python files,
1,687,480 Python LOC, ~21 s cold):

| added operation | cost |
|---|---|
| the 23 stage context managers a default offline run enters | 15.9 µs |
| `_record_counters`, dominated by one pass over 8,840 inventoried files | 1,100.5 µs |
| `snapshot()`, only when reporting | 3.3 µs |
| **total** | **~1.12 ms, or 0.005% of the run** |

The counter row is the whole of `engine._record_counters`, not the `count()` calls
inside it: those are nine calls taking under a microsecond together, and the cost
is `repo.python_files()` walking the inventory once. It is charged here in full
rather than split, because the pass exists only to be counted.

Measured directly, per operation. Establishing the same figure by subtracting
two whole-run timings does not work on a loaded developer machine: the spread
within a single arm reached 4.6%, which cannot resolve a 2% effect. Interleaved
A/B on `torchtune` over 7 alternations each put the difference at −0.5%, i.e.
below the noise floor -- the manual version of what `bench/runner.py ab` now
automates.
