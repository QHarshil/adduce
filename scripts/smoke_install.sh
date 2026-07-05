#!/usr/bin/env bash
# Installation smoke test: install adduce from PyPI into a clean environment
# and exercise every surface that depends on packaged data (profiles,
# checklists, fixer templates, rule docs). Catches broken wheels that unit
# tests against the checkout cannot.
set -euo pipefail

VENV="$(mktemp -d)/adduce-smoke"
SAMPLE="$(mktemp -d)/sample-repo"
SPEC="${1:-adduce}"   # pass adduce==X.Y.Z to test a specific release

cleanup() { rm -rf "$(dirname "$VENV")" "$(dirname "$SAMPLE")"; }
trap cleanup EXIT

PYTHON="$(command -v python3 || command -v python)"

echo "==> creating clean venv"
"$PYTHON" -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip
echo "==> installing $SPEC from PyPI"
"$VENV/bin/pip" install --quiet "$SPEC"

echo "==> version"
"$VENV/bin/adduce" --version

echo "==> rule registry (packaged rule metadata)"
RULES_OUT="$("$VENV/bin/adduce" rules)"
grep -q "R-DET-001" <<< "$RULES_OUT"

echo "==> explain (rule docs)"
EXPLAIN_OUT="$("$VENV/bin/adduce" explain R-DET-001)"
grep -qi "seed" <<< "$EXPLAIN_OUT"

echo "==> building sample repository"
mkdir -p "$SAMPLE/configs"
cat > "$SAMPLE/train.py" <<'PY'
import torch
loader = torch.utils.data.DataLoader(None, shuffle=True)
PY
printf 'torch==2.1.0\n' > "$SAMPLE/requirements.txt"
printf 'lr: 0.001\n' > "$SAMPLE/configs/main.yaml"
printf '# sample\n\n## Installation\n\npip install -r requirements.txt\n' > "$SAMPLE/README.md"

echo "==> check (profiles TOML must load)"
"$VENV/bin/adduce" check "$SAMPLE" --format json | "$VENV/bin/python" -c "
import json, sys
payload = json.load(sys.stdin)
assert 0 <= payload['total'] <= 100, payload['total']
assert payload['findings'], 'no findings produced'
print(f\"    score {payload['total']}, {len(payload['findings'])} findings\")
"

echo "==> checklist (bundled YAML must load)"
CHECKLIST_OUT="$("$VENV/bin/adduce" checklist "$SAMPLE" --profile neurips)"
grep -q "NeurIPS" <<< "$CHECKLIST_OUT"

echo "==> fix scaffold (Jinja templates must ship)"
"$VENV/bin/adduce" fix "$SAMPLE" --scaffold seeds
test -f "$SAMPLE/seed_utils.py"
"$VENV/bin/python" -c "compile(open('$SAMPLE/seed_utils.py').read(), 'seed_utils.py', 'exec')"

echo "==> export (archival renderers)"
"$VENV/bin/adduce" export codemeta "$SAMPLE"
test -f "$SAMPLE/codemeta.json"

echo "==> badge"
BADGE_OUT="$("$VENV/bin/adduce" badge "$SAMPLE" --svg)"
grep -q "<svg" <<< "$BADGE_OUT"

echo "PASS: $SPEC installs and runs from PyPI"
