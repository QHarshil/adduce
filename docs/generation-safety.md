# Generation safety contract

adduce generates artifacts that researchers may submit or rely on: repository-evidence drafts for NeurIPS and ACL checklist items, an ACM Artifact Appendix draft, the reproducibility manifest, `CITATION.cff`, archival metadata (RO-Crate, Croissant, CodeMeta), and the `reproduce.sh` scaffold. These outputs are drafts for author review, never final claims or substitutes for review.

The failure mode this document guards against is specific: a plausible-but-wrong "yes" on a submission checklist is a liability the author inherits, in front of reviewers, with their name on it. A tool that confidently fills in answers it cannot support is worse than no tool. adduce's value is the opposite of a confidence machine: it says exactly what it found, where it found it, and what it could not find.

Checklist and appendix answers are governed by the evidence-ledger policy below and can be checked afterward with `adduce audit-generated`. Scaffolds and archival exports use explicit author-review markers where repository evidence cannot supply a value.

## The ten principles

1. **Drafts, not submissions.** Every generated artifact is a draft for the author to review, correct, and own. Checklist and appendix generation also prints a safety summary.

2. **Every confident answer cites evidence.** A drafted `yes` requires source-located, high-confidence evidence or a directly relevant author-confirmed manifest field. Inferred or incomplete evidence remains `partial`; information outside the repository is marked author-input-required.

3. **"Detected", never "guaranteed".** adduce reports what static analysis observed. Generated wording is of the form "a Dockerfile was detected at `Dockerfile:1`", never "the environment is reproducible".

4. **No execution claims without execution.** Static analysis can establish that a plausible evidence trail exists—a command, config, and log that agree. It cannot establish that running the command reproduces the result. `adduce reproduce` records a separate dynamic report; checklist and appendix generation does not currently import that report, so those drafts never claim execution verification.

5. **Nothing is invented.** Where evidence is absent, the generated answer is "not detected" or "author input required" — never a filled-in guess. Wording prefers "not detected" over "absent": absence is only stated when it is provable (a required file missing from an exhaustively enumerated location), and "we did not find it where we looked" is the honest default.

6. **Conflicts surface; they are never silently resolved.** When the paper says X and a config says Y, the generated answer is `partial` or `unknown` and the artifact shows both sides of the conflict with their locations. Picking a winner is the author's call, because either side could be the correct one.

7. **Author content is preserved.** Manifest refresh writes a separate proposal that fills blanks or appends detected entries; the original YAML, comments, and extensions remain untouched. Fix scaffolds create new files or append missing README sections and skip existing files.

8. **Source is never edited silently.** The only codemods that rewrite source are the remote-pinning edits (`adduce pin-remotes`), and they require an explicit `--write` after showing the diff. The paper `.tex` is never edited automatically under any flag, because when paper and code drift, either side could be the correct one.

9. **Secrets are never echoed.** When a committed credential is detected, generated output records its location and kind only ("AWS access key detected at `deploy/config.py:12`"). The value never appears in any artifact, log, or ledger.

10. **LLM prose is post-hoc and optional.** The bring-your-own-key layer receives deterministic finding summaries, not source files, and can draft labelled justification prose. It never determines the evidence-ledger answer. Provider prose remains untrusted author-review material; everything works identically with no provider configured.

## Answer levels

Evidence-ledger entries for generated checklists and appendices use exactly five answer values:

| Answer | Meaning |
|---|---|
| `yes` | Direct, high-confidence evidence supports the answer. |
| `partial` | Some evidence exists but it is incomplete, inferred, conflicting, or low-confidence. |
| `not_detected` | The expected evidence was searched for in recorded locations and not found. |
| `author_input_required` | The answer depends on information outside the repository — IRB approval, human-subjects protocols, funding disclosures. |
| `unknown` | Evidence exists but is too ambiguous to classify safely. |

The distinctions matter in practice:

- No Dockerfile after scanning the repository root and the conventional locations is `not_detected` — adduce looked and did not find one, and the ledger records where it looked.
- "Did this research involve human subjects?" is `author_input_required` — no amount of repository scanning can answer an IRB question, and pretending otherwise would be an invented claim.
- Two metric tables in `results/` that disagree about the headline number is `unknown` or `partial`, with both locations shown — the evidence exists, but resolving it requires knowing which run the paper reports.

`not_detected` is a statement about a search, not about the world. `author_input_required` is a statement that the search cannot apply. `unknown` is a statement that the search returned something adduce declines to interpret alone.

## Negative evidence records the checks consulted

"Not detected" is tied to the rule IDs that performed the search. The ledger also records each rule's missing-evidence message. Rule documentation (`adduce explain <rule-id>`) defines the files and signals that rule inspects.

```json
{
  "item_id": "container",
  "answer": "not_detected",
  "searched": ["R-ENV-003"],
  "missing": ["No Dockerfile, devcontainer, or conda environment definition detected."]
}
```

This makes the negative answer traceable to a stable rule definition rather than presenting an unexplained "no".

## Confidence thresholds

The mapping from evidence to answer is an explicit, versioned policy — not a judgement call made per item.

**Default generation:**

| Answer | Requires |
|---|---|
| `yes` | Direct evidence with confidence ≥ 0.85, or `manifest_author_confirmed` evidence |
| `partial` | Inferred evidence, conflicting evidence, or confidence in [0.50, 0.85) |
| `not_detected` | A recorded search that found nothing |
| `author_input_required` | No repository-observable evidence can answer the item |
| `unknown` | Evidence exists but cannot be safely interpreted |

**Strict-evidence mode (`--strict-evidence`):**

| Answer | Requires |
|---|---|
| `yes` | Direct or `manifest_author_confirmed` evidence with confidence ≥ 0.90 |
| `partial` | Direct evidence below the threshold |
| `author_input_required` | Any answer that would otherwise rest on inferred evidence alone |

Strict-evidence mode exists for artifacts headed straight into a submission: it converts every inference into an explicit author decision.

## The evidence ledger

Every checklist or appendix generation updates `.adduce/evidence-ledger.json`, which records, per answer:

- the answer value;
- each evidence item, with its kind, path, line, and confidence;
- the evidence strength — one of `direct`, `inferred`, `manifest_author_confirmed`, `online_resolved`, or `dynamic_verified`;
- the searched scope;
- an explicit `missing` list.

The ledger also records generation provenance: `adduce_version`, command, profile, mode, repository commit, and generation time.

```json
{
  "checklist-neurips.md": {
    "artifact_path": "checklist-neurips.md",
    "artifact_sha256": "...",
    "provenance": {
      "adduce_version": "0.1.0",
      "command": "checklist",
      "profile": "neurips",
      "mode": "default",
      "repo_commit": "3adf61e9b2c14f70a8d3e5c6b1f42a9d0e7c8b21",
      "generated_at": "2026-07-04T14:12:09Z"
    },
    "generated_text_policy": "evidence_only",
    "entries": [
      {
        "item_id": "experimental-reproducibility",
        "answer": "partial",
        "evidence": [
          {
            "kind": "R-DET-001",
            "path": "train.py",
            "line": 37,
            "confidence": 0.78,
            "strength": "inferred"
          }
        ],
        "searched": ["R-DOC-001", "R-DOC-002", "R-DOC-003", "R-DET-001", "R-EXEC-003"],
        "missing": ["NumPy and Python RNG seeds were not detected."],
        "conflicts": []
      }
    ]
  }
}
```

Generated text is downstream of deterministic evidence, not the source of truth.

## Generated prose is intentionally plain

Deterministic evidence text is factual and avoids persuasive language about the artifact: "seeding was detected for torch (`train.py:37`); NumPy and Python RNGs were not" is the intended register, not "the code is carefully seeded."

Optional provider-generated prose is visibly labelled as a draft and does not alter the deterministic answer or ledger. Authors should omit `--llm` when they require a wholly deterministic artifact, and must review provider prose when they enable it.

## Human-edit markers

Generated drafts carry visible markers so review points are unmissable rather than buried:

- `[AUTHOR REVIEW REQUIRED]` on author-input answers, conflicts, and scaffold fields the repository cannot supply;
- `[EVIDENCE: README.md:84, train.py:37]` anchors where a finding has source locations; the ledger retains inferred evidence that has no line-level anchor.

Markers survive regeneration and are what `adduce audit-generated` looks for when checking that nothing unresolved slipped through.

## No silent success — the generated artifact summary

Checklist, appendix, and package generation print a summary rather than exiting quietly:

- how many answers are evidence-backed;
- how many are partial;
- how many require author input;
- how many conflicts were surfaced;
- the path of the evidence ledger.

The summary states plainly that an artifact with conflicts or author-input-required fields is useful but not submission-ready. A generation run that produces output is not the same as a generation run that produced a finished artifact, and the summary is where that difference is kept honest.

## Self-audit

`adduce audit-generated <path>` runs dedicated generation-safety checks against the artifact and its ledger:

| Rule | Fires when |
|---|---|
| `R-GEN-001` | A generated claim lacks an evidence path |
| `R-GEN-002` | A "yes" rests on low-confidence evidence |
| `R-GEN-003` | Generated text implies execution-based verification but no reproduce run is recorded |
| `R-GEN-004` | An unresolved placeholder remains in the artifact |
| `R-GEN-005` | The artifact changed since its ledger was produced |

This audits the audit output; run it before anything generated is submitted.

## Red-team expectations

The synthetic corpus includes permanent regression cases whose correct behaviour is *not* a confident answer:

- the paper claims results over N seeds while the code sets exactly one;
- the README claims Docker support with no Dockerfile anywhere in the repository;
- the paper claims the data is public but the link resolves to a private location;
- a reported metric appears only in a table unrelated to the claim;
- a config value exists but is not tied to any claim in the manifest or the paper.

For each of these, the expected generated answer is `partial`, `unknown`, or `author_input_required` — never `yes`. A change that upgrades any of them to a confident answer fails the corpus gate, whatever it does for the rest of the suite. Declining to answer confidently in these cases is the behaviour under test.
