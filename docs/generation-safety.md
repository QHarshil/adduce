# Generation safety contract

adduce generates artifacts that researchers submit or rely on: filled NeurIPS and ACL checklists, the ACM Artifact Appendix, the reproducibility manifest, `CITATION.cff`, archival metadata (RO-Crate, Croissant, CodeMeta), and the `reproduce.sh` scaffold. Because these artifacts may enter real submissions, adduce treats every generated statement as an evidence-backed draft — never a final claim, and never a substitute for author review.

The failure mode this document guards against is specific: a plausible-but-wrong "yes" on a submission checklist is a liability the author inherits, in front of reviewers, with their name on it. A tool that confidently fills in answers it cannot support is worse than no tool. adduce's value is the opposite of a confidence machine: it says exactly what it found, where it found it, and what it could not find.

Every rule below is enforced by the generation code and checkable after the fact. None of it is a disclaimer bolted on.

## The ten principles

1. **Drafts, not submissions.** Every generated artifact is a draft for the author to review, correct, and own. Output wording, file headers, and the generation summary all say so.

2. **Every claim cites evidence or is marked for the author.** Each generated answer either carries `file:line` evidence anchors or is explicitly marked author-input-required. There is no third path; an unsupported "yes" is a bug, not a convenience.

3. **"Detected", never "guaranteed".** adduce reports what static analysis observed. Generated wording is of the form "a Dockerfile was detected at `Dockerfile:1`", never "the environment is reproducible".

4. **No execution claims without execution.** Static analysis can establish that a plausible evidence trail exists — a command, a config, a log that agree with each other. It cannot establish that running the command reproduces the result. Only a passing `adduce reproduce` run permits execution-based wording ("the smoke target was executed twice and the runs agreed"), and that wording names the run it rests on.

5. **Nothing is invented.** Where evidence is absent, the generated answer is "not detected" or "author input required" — never a filled-in guess. Wording prefers "not detected" over "absent": absence is only stated when it is provable (a required file missing from an exhaustively enumerated location), and "we did not find it where we looked" is the honest default.

6. **Conflicts surface; they are never silently resolved.** When the paper says X and a config says Y, the generated answer is `partial` or `unknown` and the artifact shows both sides of the conflict with their locations. Picking a winner is the author's call, because either side could be the correct one.

7. **Author content is preserved.** The manifest and the fix scaffolds are append-only or diff-gated: regeneration adds detected material or presents a diff, and never clobbers content the author has confirmed or edited.

8. **Source is never edited silently.** The only codemods that rewrite source are the remote-pinning edits (`adduce pin-remotes`), and they require an explicit `--write` after showing the diff. The paper `.tex` is never edited automatically under any flag, because when paper and code drift, either side could be the correct one.

9. **Secrets are never echoed.** When a committed credential is detected, generated output records its location and kind only ("AWS access key detected at `deploy/config.py:12`"). The value never appears in any artifact, log, or ledger.

10. **LLM prose is post-hoc and optional.** The bring-your-own-key layer only rephrases already-produced, evidence-linked findings into justification prose. It never determines a yes/no answer, never adds a claim, and never sees content beyond the findings it is rephrasing. Everything works identically with no key configured.

## Answer levels

Generated checklists and appendices use exactly five answer values. No other answer is ever emitted.

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

## Negative evidence requires search scope

adduce may only generate "missing" or "not detected" wording when the evidence ledger records which locations and evidence types were searched. A negative claim without a recorded search scope is unfalsifiable, so it is not allowed: if the scope was not recorded, the wording stays at "not detected" and the ledger entry is marked incomplete.

```json
{
  "item": "neurips.q12.container",
  "value": "not_detected",
  "searched": [
    {"kind": "file", "patterns": ["Dockerfile", "*.dockerfile", "docker/Dockerfile"]},
    {"kind": "file", "patterns": ["environment.yml", "environment.yaml"]},
    {"kind": "config", "patterns": ["devcontainer.json", ".devcontainer/**"]}
  ],
  "missing": ["container definition"]
}
```

This makes every negative answer auditable: the author (or `adduce audit-generated`) can check whether the search was broad enough before trusting the "no".

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

Every generated checklist or appendix is written alongside `.adduce/evidence-ledger.json`, which records, per answer:

- the answer value;
- each evidence item, with its kind, path, line, and confidence;
- the evidence strength — one of `direct`, `inferred`, `manifest_author_confirmed`, `online_resolved`, or `dynamic_verified`;
- the searched scope;
- an explicit `missing` list.

The ledger also records generation provenance: `adduce_version`, the exact `command`, the `profile`, the `mode`, the `repo_commit`, and `generated_at`.

```json
{
  "adduce_version": "1.4.2",
  "command": "adduce checklist --profile neurips",
  "profile": "neurips",
  "mode": "default",
  "repo_commit": "3adf61e9b2c14f70a8d3e5c6b1f42a9d0e7c8b21",
  "generated_at": "2026-07-04T14:12:09Z",
  "answers": [
    {
      "item": "neurips.q7.seeds",
      "value": "partial",
      "strength": "inferred",
      "evidence": [
        {"kind": "code", "path": "train.py", "line": 37, "confidence": 0.78,
         "note": "torch.manual_seed(cfg.seed) detected; numpy and python RNGs not seeded"}
      ],
      "searched": [
        {"kind": "code", "patterns": ["**/*.py"], "signals": ["manual_seed", "random.seed", "np.random.seed"]}
      ],
      "missing": ["numpy seed", "python random seed"]
    },
    {
      "item": "neurips.q12.container",
      "value": "not_detected",
      "strength": "direct",
      "evidence": [],
      "searched": [
        {"kind": "file", "patterns": ["Dockerfile", "*.dockerfile", "docker/**", "environment.yml"]}
      ],
      "missing": ["container definition", "conda environment"]
    },
    {
      "item": "neurips.q3.irb",
      "value": "author_input_required",
      "strength": "direct",
      "evidence": [],
      "searched": [],
      "missing": [],
      "note": "Not derivable from the repository."
    }
  ]
}
```

This is what distinguishes adduce from an LLM checklist assistant: the generated text is downstream of deterministic evidence, not the source of truth.

## Generated prose is intentionally plain

Generated justification text is factual and evidence-bound. It contains no persuasive or flattering language about the artifact, and it never inflates confidence — "seeding was detected for torch (`train.py:37`); numpy and python RNGs were not" is the register, not "the code is carefully seeded". Wording strength is capped by evidence strength: inferred evidence produces hedged sentences, and only `dynamic_verified` evidence produces execution wording.

This applies doubly when the optional LLM layer is enabled. LLM-phrased prose is checked against the same constraint — it rephrases findings, and any output that strengthens a claim beyond its evidence is rejected in favour of the deterministic wording.

## Human-edit markers

Generated drafts carry visible markers so review points are unmissable rather than buried:

- `[AUTHOR REVIEW REQUIRED]` on every author-input answer and every conflict, so a search for that string finds all outstanding decisions;
- `[EVIDENCE: README.md:84, train.py:37]` anchors on every evidence-backed statement, so each claim can be checked at its source in seconds.

Markers survive regeneration and are what `adduce audit-generated` looks for when checking that nothing unresolved slipped through.

## No silent success — the generated artifact summary

Every generation command ends by printing a summary rather than exiting quietly:

- how many answers are evidence-backed;
- how many are partial;
- how many require author input;
- how many conflicts were surfaced;
- how many placeholders remain unresolved;
- whether dynamic execution (`adduce reproduce`) contributed evidence;
- the path of the evidence ledger.

The summary states plainly that an artifact with conflicts or author-input-required fields is useful but not submission-ready. A generation run that produces output is not the same as a generation run that produced a finished artifact, and the summary is where that difference is kept honest.

## Self-audit

`adduce audit-generated <path>` checks a generated artifact against its ledger, using the same finding machinery as everything else:

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
