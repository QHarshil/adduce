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
- **Peak memory is measured one target per process.** It is a process
  high-water mark, so measuring several targets in one interpreter would report
  the largest of them for all of them.
- **A repeat run is not a warm run.** There is no analyzer cache yet, so
  `repeat_runtime_seconds` differs from cold only by operating-system page
  cache. Every record carries `warm_path_exists: false` until that changes.
- **Size strata are assigned from measured Python LOC**, never declared in the
  manifest.

## Running it

```console
python bench/runner.py measure --output bench/reports/local.json
python bench/runner.py measure --output bench/reports/one.json --only torchtune
python bench/runner.py compare --baseline bench/reports/baseline.json \
                               --current bench/reports/local.json
python bench/runner.py finding-diff --only adduce-self
```

`compare` exits non-zero when, against the baseline, a repeated run stops being
byte-identical, parser failures rise, a synthetic-corpus score moves, cold
runtime regresses beyond 25%, or disk reads per inventoried file rise.

`finding-diff` runs each target with the ignore file honoured and ignored, and
enumerates every rule status that moves between the two. Each move is classified
as a rule that stopped applying, became not-applicable, dropped, or improved —
producing no finding at all is a different fact from reaching a new conclusion,
so the two are never merged. This is the evidence for honouring `.gitignore` by
default, and on the adduce repository it reports 30 moves: 9 / 13 / 7 / 1 in that
order.

Both arms pass the ignore setting explicitly, so a change to the shipped default
cannot silently move a measurement or a baseline comparison. Note that the
`default` arm of a `measure` report therefore pins the whole-tree scan, which is
no longer what `adduce check` does without arguments.

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
below the noise floor.
