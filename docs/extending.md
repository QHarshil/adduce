# Extending adduce

Rules and reporters are discovered through entry points — the flake8/pytest
pattern. A lab rule pack is an ordinary package:

```python
# my_lab_rules.py
from adduce.rules import Category, Rule, Status

class SlurmScriptRule(Rule):
    id = "R-LAB-001"
    category = Category.CODE_EXECUTION
    title = "SLURM submission script present"
    rationale = "Our cluster reproductions start from a submit script."
    weight = 3

    def evaluate(self, ev):
        scripts = ev.repo.find("slurm/*.sh") + ev.repo.find("*.sbatch")
        if scripts:
            return self.finding(Status.PASS, 0.9, f"Found {scripts[0].path}.")
        return self.finding(Status.FAIL, 0.8, "No SLURM script found.",
                            remediation="Add slurm/submit.sh for the main experiment.")

RULES = [SlurmScriptRule]
```

```toml
[project.entry-points."adduce.rules"]
my_lab = "my_lab_rules"
# reporters: [project.entry-points."adduce.reporters"]  name = "module:render"
```

Installing the pack is all it takes.

## Attaching item detail

A rule that checks many individual things — one row per declared artifact,
one row per citation — can attach a `FindingItem` per observation instead of
collapsing them into one message. The parent `Finding` stays the scored unit;
items only explain it:

```python
from adduce.rules import Category, FindingItem, Rule, Status

class DeclaredArtifactsRule(Rule):
    id = "R-LAB-002"
    category = Category.DATA
    title = "Declared artifacts are present"
    rationale = "Every path listed in artifacts.txt should exist on disk."
    weight = 2

    def evaluate(self, ev):
        names = (ev.repo.read_text("artifacts.txt") or "").splitlines()
        items = [
            FindingItem(
                id=name,
                status=Status.PASS if ev.repo.exists(name) else Status.FAIL,
                message="found" if ev.repo.exists(name) else "missing",
            )
            for name in names if name
        ]
        missing = sum(1 for item in items if item.status is Status.FAIL)
        summary = (
            f"{missing} of {len(items)} declared artifacts are missing."
            if missing else "All declared artifacts are present."
        )
        return self.finding(
            Status.FAIL if missing else Status.PASS, 0.9, summary, items=items,
        )
```

`items` is keyword-only and optional; a rule that never passes it is
unaffected. See [Finding items](plugin-api.md#finding-items) for the
constructor's full field list, the resource envelope, and how each report
format serialises children.

This rule reads the filesystem directly inside `evaluate`
(`ev.repo.read_text`, `ev.repo.exists`), which no built-in rule does. It
illustrates a known gap in the purity contract, not the intended pattern: the
plugin API has no entry-point group for adding a collector, so a third-party
rule that needs new evidence has no in-contract way to get it. Nothing in
adduce stops the read shown here, but see
[Rule purity](plugin-api.md#rule-purity) and
[ADR 0004](adr/0004-rule-purity-and-output-ownership.md) before writing one
like it.
