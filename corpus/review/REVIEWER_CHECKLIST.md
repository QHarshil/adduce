# Claim review — reviewer checklist

Work through this alongside [`REVIEWER_GUIDE.md`](REVIEWER_GUIDE.md), which
explains every item. The guide governs where the two differ.

## Before starting

- [ ] The packet verifies: `python -B corpus/scripts/reviewer_packet.py verify
      --packet <your-packet-dir>` exits 0.
- [ ] The workspace is created with `init`, using your own reviewer identifier
      and a domain-expertise statement you can stand behind.
- [ ] You have read the decision vocabulary, including what `verified` means
      for a link recorded as unresolved.
- [ ] You hold no Adduce claim-link output for these repositories, and you have
      not seen the other reviewer's decisions.
- [ ] You can make all four conflict-of-interest affirmations for every claim
      in the assignment. Where you cannot, that claim is raised with the
      coordinator for reassignment before anything is recorded.
- [ ] You know the route for returning the completed file and for asking
      technical questions.

## For each claim

- [ ] The blinding and conflict-of-interest declarations for this claim are
      recorded with `declare`, before the first decision for it.
- [ ] The clone at `corpus/clones/pilot-2026-07-13/<repo_id>` is the repository
      the record names, and is at the `repo_commit` the record names.
- [ ] The `source` quote is checked against the recorded path and line range at
      that commit, and is faithful to what is there.
- [ ] The claim-level decision is recorded with a rationale and at least one
      evidence locator.
- [ ] All ten link targets are reviewed: `code`, `reported_result`, `run`,
      `output`, `command`, `configuration`, `data`, `environment`, `seed`,
      `commit`.
- [ ] Each link decision judges whether the recorded expectation is correct,
      not whether an artifact happened to be found.
- [ ] A link correctly recorded as unresolved is decided `verified`, not
      `revision_required`.
- [ ] No decision infers that a command, run, or output executed from static
      documentation describing it.
- [ ] Every decision has a rationale that names what you inspected.
- [ ] Every decision has at least one evidence locator, in `path:line` form
      where the pinned repository can carry it.
- [ ] Every `unclear` decision names the missing or conflicting evidence.
- [ ] No Adduce output was consulted, and no other reviewer's decisions were
      consulted.
- [ ] No tool suggestion, script output, or model assessment was recorded as
      your decision.

## Before finalizing a claim

- [ ] `show` reads back the record you intended to review, and the claim
      identifier matches.
- [ ] Eleven decisions exist for this claim: one claim-level and ten
      link-level.
- [ ] Any value you meant to change was cleared with `clear-field` and
      re-entered.
- [ ] No rationale or locator contains a credential, token, private URL, or
      copied secret value.
- [ ] No declaration contains a name, contact detail, employer, or
      relationship detail.
- [ ] `finalize-claim` exits 0 for this claim.

## Before returning the complete file

- [ ] `status` reports
      `claims=10 completed=10 decisions=110/110 declarations=10/10`.
- [ ] `finalize-review` exits 0 and writes the completed file.
- [ ] Every declaration timestamp precedes the decisions it covers.
- [ ] Process burden is recorded and submitted with
      `corpus/scripts/reviewer_feedback.py`, separately from the review file.
- [ ] The completed file goes to the coordinator only. It is not sent to the
      other reviewer and not placed anywhere the other reviewer can read.
- [ ] The workspace file is kept until the coordinator confirms receipt.
