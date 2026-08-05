# Claim review — reviewer guide

You are one of two independent domain reviewers for a preregistered pilot
study. The study has frozen a claim record: for each of ten research
repositories it records one headline claim, the exact source lines that claim
was taken from, and ten expected links from that claim to the artifacts that
would support it. Your task is to judge whether that frozen record is correct
at the pinned commit.

> The reviewer is evaluating the frozen claim record. The reviewer is not
> evaluating Adduce's claim-link output during this gate.

## What is and is not under review

Under review: the accuracy of the frozen record. Whether the quoted claim text
is faithful to the source at the recorded lines, whether the recorded location
and identifiers are right, and whether each of the ten recorded link
expectations holds at the pinned commit.

Not under review: the scientific merit of the repository or its authors;
whether the repository reproduces its published numbers; whether the
repository is well engineered; and the behaviour of Adduce. You will see no
Adduce output during this gate, and you should not seek any out. If you
encounter Adduce output for one of these repositories by accident, stop and
tell the coordinator before recording anything further.

Two conditions make your review usable: you work independently of the other
reviewer, and you work without Adduce's claim-link output for this claim
record. You will affirm both in a declaration recorded in your own file, so
keep them true for the whole assignment.

## Your packet

The coordinator hands you a self-contained directory containing this guide,
the checklist, a packet manifest, the frozen claim file, the claim-review and
reviewer-workspace schemas, an empty review scaffold bound to this study, and
a verification wrapper. Check the packet before you start:

```console
python -B corpus/scripts/reviewer_packet.py verify --packet <your-packet-dir>
```

Exit 0 means the packet is complete and its contents match the manifest. If it
does not exit 0, ask the coordinator for a replacement rather than editing
anything by hand.

## The ten claims and the pinned repositories

There are ten claims, one per repository, and both reviewers review all ten.
The work is not split between you; the point of the design is two independent
judgements on the same material.

Each claim names its repository and a 40-character `repo_commit`. The pinned,
read-only clone of that repository is at
`corpus/clones/pilot-2026-07-13/<repo_id>`, already checked out at that
commit. Every claim's source is that repository's `README.md` at the pinned
commit. Do not fetch, pull, or check out a different commit; the clones are
frozen study inputs and the decision is about the pinned state. You may consult
the upstream project for context, but the evidence you cite must be locatable
at the pinned commit.

Two of the ten claims are non-numeric by design and must not be treated as
extraction errors. One is a broad comparative statement; the truth record's own
`context` field says the source states no single effect size, so the absence of
a number is recorded rather than missed. The other is an
implementation-capability statement whose source spells the project's name
differently from the project's usual spelling; the truth record documents this,
and the quote is faithful to the source. Neither is grounds on its own for
`revision_required`.

## The 110 decisions

Ten claims, and for each claim one claim-level decision plus ten link-level
decisions. Ten times eleven is 110 decisions.

**The claim-level decision** covers the claim record as a whole: the claim
identifier, repository identifier and commit, the `source` block with its path,
digest, quote and line range, and the `claim` block with its text, metric,
value, unit and context. Ask whether this is a faithful and correctly located
record of a headline claim in the pinned repository.

**The ten link-level decisions** cover the ten targets, which appear in every
record in this order:

`code` · `reported_result` · `run` · `output` · `command` ·
`configuration` · `data` · `environment` · `seed` · `commit`

Each link records its `target`, an `expected_resolution`, any `artifacts` the
record associates with it, and a `rationale`. Ask whether that recorded
expectation is correct at the pinned commit. The question is not "does an
artifact exist"; it is "is the record right about it".

## The decision vocabulary

Exactly three values are permitted, for both the claim-level and the
link-level decisions.

| Value | Use when |
| --- | --- |
| `verified` | The recorded expectation is correct at the pinned commit. |
| `revision_required` | The recorded expectation is wrong and the record must change. |
| `unclear` | You cannot resolve it from the accessible pinned evidence. |

The most-missed rule in this vocabulary: `verified` means the record is right,
not that an artifact was found. When a link records that the expected artifact
is not resolvable at the pinned commit, and that is true, the correct decision
is `verified`. Marking such a link `revision_required` because you found
nothing inverts the question and is the single most common way a good record is
wrongly rejected. The reverse also holds: if a link records an artifact as
resolvable and it is absent, or the recorded artifact is the wrong one, that is
`revision_required`.

`unclear` is a real answer and it is neither a failure nor a pass. Use it when
the pinned evidence genuinely cannot settle the question, and record what you
checked and what was missing or in conflict. Do not use it to avoid a hard
link.

## Documented is not executed

A repository file that shows a training command is evidence that a command is
*documented*. It is not evidence that the command ran, that it produced the
reported number, or that any output in the repository came from it. This
applies most often to the `run`, `output`, `command` and `seed` targets. Do not
infer execution from static documentation, and do not let a plausible narrative
in a README stand in for an artifact. Equally, the absence of a run record is
not evidence that a run failed; it is absence of evidence, and the record
should say so.

## Writing a rationale

Every decision — claim-level and link-level — requires a rationale. Write one
to three sentences that state what you checked and why the recorded expectation
does or does not hold. A rationale should be readable by someone who does not
have the repository open.

- Name the thing you inspected, not only the conclusion.
- Do not restate the decision word ("verified because it is verified").
- For `revision_required`, say what the record asserts and what the pinned
  evidence shows instead.
- For `unclear`, name the specific evidence that is missing or in conflict.
  This is required, not stylistic.

## Writing an evidence locator

Every decision requires at least one evidence locator, and the locator must
support the decision rather than merely identify the repository. Follow the
evidence-preference order in
[`../ANNOTATION_GUIDE.md`](../ANNOTATION_GUIDE.md) under "Evidence and
confidence":

1. a pinned repository path with a line or object locator;
2. a hashed local snapshot of a paper, result, configuration, or run output;
3. an authoritative URL with the relevant version and the date you read it;
4. a retained author-confirmation record with its date and locator.

Prefer form 1. Write `path:line` or `path:line_start-line_end` relative to the
repository root — a bare filename is not a locator. To illustrate the shape
only: for a `configuration` link in a fictional repository `example-repo`, a
usable locator is `configs/example.yaml:12-18`, and a second locator such as
`train.py:44` may be added when two files together carry the evidence. Record
conflicting evidence rather than choosing the convenient source. Never include
credentials, tokens, private URLs, or copied secret values in a locator or a
rationale.

You may use tooling to read and search the repository. You may not record a
tool's suggestion, a script's output, or a model's assessment as your decision.
The decision and its rationale are yours.

## The two declarations

Two time-stamped declarations must exist before your first decision for a given
claim. The entry tool refuses a decision for an undeclared claim, and the
timestamps are checked.

**Blinding declaration** — `independent_review`,
`other_reviewer_decisions_not_seen`, `adduce_claim_link_outputs_not_seen`,
`declared_at`.

**Conflict-of-interest declaration** — a `scope` of
`{repository_id, artifact_id}`, plus
`no_relevant_authorship_or_contribution`,
`no_close_collaboration_supervision_or_employment`, `no_financial_conflict`,
`no_personal_conflict`, and `declared_at`.

Declare per claim as you reach it, or declare all ten before you start. Either
order is acceptable. Declaring after a decision is not, and cannot be repaired
afterwards.

Declarations carry booleans and assignment scope only. They must not contain a
name, contact detail, employer, or relationship detail; the schema rejects
extra fields, and this is a privacy rule as much as a formatting one. Your
stable, non-personal reviewer identifier is the only identity in the file,
alongside the domain-expertise statement you record at `init`.

## Conflict of interest and recusal

Before deciding a claim, affirm that for that exact repository and claim you
have no relevant authorship or contribution; no close collaboration,
supervision, or employment involving its authors or contributors; no financial
interest that could reasonably affect your judgement; and no personal
relationship or dispute that could reasonably affect your judgement.

If any affirmation cannot be made, stop. Record nothing for that claim and tell
the coordinator that the assignment needs to be reassigned. Do not record the
reason in the artifact, and do not record a disclosure and continue —
disclosure is not a substitute for recusal, and there is no path that keeps a
conflicted decision in the record. The declaration is scoped per assignment, so
recusal from one repository does not remove you from the others.

## Using the reviewer-entry tool

All decisions are entered through `corpus/scripts/claim_review_entry.py`, which
writes a workspace file and, at the end, your completed review. Its commands
are `init`, `status`, `show`, `declare`, `record-claim`, `record-link`,
`clear-field`, `finalize-claim`, `finalize-review`, and `verify`.

Create the workspace once, from the packet's scaffold and claim file:

```console
python -B corpus/scripts/claim_review_entry.py init \
  --scaffold review-scaffold.json \
  --claims pilot-claims.json \
  --reviewer-id <your-reviewer-id> \
  --domain-expertise "<your expertise statement>" \
  --workspace <your-reviewer-id>.review-workspace.json
```

Read one claim record:

```console
python -B corpus/scripts/claim_review_entry.py show \
  --workspace <your-reviewer-id>.review-workspace.json \
  --claims pilot-claims.json --claim-id <claim-id>
```

Declare before deciding that claim. All seven affirmations are given together,
and the tool records both declarations with their timestamps:

```console
python -B corpus/scripts/claim_review_entry.py declare \
  --workspace <your-reviewer-id>.review-workspace.json --claim-id <claim-id> \
  --affirm-independent-review \
  --affirm-other-reviewer-decisions-not-seen \
  --affirm-adduce-claim-link-outputs-not-seen \
  --affirm-no-relevant-authorship-or-contribution \
  --affirm-no-close-collaboration-supervision-or-employment \
  --affirm-no-financial-conflict \
  --affirm-no-personal-conflict
```

Record the claim-level decision with `record-claim`, and each link with
`record-link`. Pass `--evidence` once per locator:

```console
python -B corpus/scripts/claim_review_entry.py record-link \
  --workspace <your-reviewer-id>.review-workspace.json --claim-id <claim-id> \
  --target configuration --decision verified \
  --rationale "<what you checked and why the record holds>" \
  --evidence "<path:line>" --evidence "<path:line>"
```

`clear-field` removes a value you want to re-enter. `finalize-claim` closes one
claim once its declaration, claim-level decision and all ten link decisions are
present. Work claim by claim; finalizing as you go makes an interrupted session
easy to resume.

## Status and final validation

`status` prints one line:

```console
python -B corpus/scripts/claim_review_entry.py status \
  --workspace <your-reviewer-id>.review-workspace.json
```

```
workspace valid: claims=10 completed=4 decisions=47/110 declarations=4/10
```

Before you return your work, `status` must report
`claims=10 completed=10 decisions=110/110 declarations=10/10`, and
`finalize-review` must exit 0:

```console
python -B corpus/scripts/claim_review_entry.py finalize-claim \
  --workspace <your-reviewer-id>.review-workspace.json \
  --claims pilot-claims.json --claim-id <claim-id>

python -B corpus/scripts/claim_review_entry.py finalize-review \
  --workspace <your-reviewer-id>.review-workspace.json \
  --claims pilot-claims.json --out completed-review.json
```

`verify` re-checks an existing workspace or completed file without changing it.

## Returning the completed review

Hand `completed-review.json` to the coordinator by the route they gave you, and
keep your workspace file until they confirm receipt. Do not send the file to
the other reviewer, do not place it in a shared location either of you can
read, and do not describe your decisions in a channel the other reviewer can
see. Your file stays inaccessible to the other reviewer for the duration of the
gate; that is what makes the two reviews independent.

Separately from the review itself, `corpus/scripts/reviewer_feedback.py`
(`init`, `record-time`, `submit`, `validate`) collects review-process burden
data: minutes per claim, how many times a validator refused an entry, how many
clarifications you had to request, four 1–5 ratings, and two free-text fields.
It never touches your decisions and collects no identifying information.
Recording your minutes as you go is more accurate than reconstructing them at
the end.

## Asking for technical support

Ask the coordinator, not the other reviewer, and ask about mechanics rather
than answers. Questions about the tool, the packet, the schema, how to phrase a
locator, or what a field means are all fine. Questions that reveal a leaning
are not: do not quote a decision, a rationale, or an evidence locator, and do
not pair a claim identifier with a description of what you found. If you are
unsure whether a question discloses a decision, phrase it about a fictional
example repository instead. A question you cannot ask without disclosing your
answer is one to raise after both files are returned.
