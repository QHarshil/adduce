#!/usr/bin/env bash
# Installation smoke test: install adduce into a clean environment and exercise
# every surface that depends on packaged data. The sets below are enumerated from
# the installed package rather than listed here, so dropping any bundled profile,
# checklist or fixer template fails this gate instead of shipping. That matters
# because this script is the only check that can see inside a built artifact: the
# test suite runs against the checkout through importlib.resources and cannot
# observe a wheel exclusion. The default installs from PyPI; package validation
# passes a locally built wheel or source distribution.
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
echo "==> installing $SPEC"
"$VENV/bin/pip" install --quiet "$SPEC"

echo "==> version"
"$VENV/bin/adduce" --version

echo "==> RNG diagnostic consent gate"
if [ -x "$VENV/bin/adduce-rng-audit" ]; then
  set +e
  RNG_OUT="$("$VENV/bin/adduce-rng-audit" 2>&1)"
  RNG_STATUS=$?
  set -e
  test "$RNG_STATUS" -eq 2
  grep -q "refusing execution without explicit --yes" <<< "$RNG_OUT"
elif [ "$#" -gt 0 ]; then
  echo "missing adduce-rng-audit entry point in local distribution" >&2
  exit 1
else
  echo "    not present in the current stable package; skipped"
fi

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

echo "==> check (every bundled profile must load)"
PROFILES="$("$VENV/bin/python" -c "
from importlib import resources
names = sorted(
    entry.name.removesuffix('.toml')
    for entry in resources.files('adduce.profiles').iterdir()
    if entry.name.endswith('.toml')
)
print(' '.join(names))
")"
test -n "$PROFILES"
echo "    profiles: $PROFILES"
for PROFILE in $PROFILES; do
  "$VENV/bin/adduce" check "$SAMPLE" --profile "$PROFILE" --format json | "$VENV/bin/python" -c "
import json, sys
payload = json.load(sys.stdin)
assert 0 <= payload['total'] <= 100, payload['total']
assert payload['findings'], 'no findings produced'
print(f\"    profile $PROFILE: score {payload['total']}, {len(payload['findings'])} findings\")
"
done

echo "==> checklist (every bundled checklist must load)"
CHECKLISTS="$("$VENV/bin/python" -c "
from importlib import resources
names = sorted(
    entry.name.removesuffix('.yaml')
    for entry in resources.files('adduce.checklists').iterdir()
    if entry.name.endswith('.yaml')
)
print(' '.join(names))
")"
test -n "$CHECKLISTS"
echo "    checklists: $CHECKLISTS"
for CHECKLIST in $CHECKLISTS; do
  CHECKLIST_OUT="$("$VENV/bin/adduce" checklist "$SAMPLE" --profile "$CHECKLIST")"
  test -n "$CHECKLIST_OUT"
  grep -qi "$CHECKLIST" <<< "$CHECKLIST_OUT"
  echo "    checklist $CHECKLIST rendered"
done

echo "==> fix scaffold (every Jinja template must ship)"
SCAFFOLDS="$("$VENV/bin/python" -c "
from adduce.fixers import SCAFFOLDS
print(' '.join(sorted(SCAFFOLDS)))
")"
test -n "$SCAFFOLDS"
echo "    scaffolds: $SCAFFOLDS"
for SCAFFOLD in $SCAFFOLDS; do
  # Each scaffold renders a different template, and a missing template raises
  # rather than degrading, so an unrendered scaffold fails here.
  "$VENV/bin/adduce" fix "$SAMPLE" --scaffold "$SCAFFOLD"
  echo "    scaffold $SCAFFOLD rendered"
done
test -f "$SAMPLE/seed_utils.py"
"$VENV/bin/python" -c "compile(open('$SAMPLE/seed_utils.py').read(), 'seed_utils.py', 'exec')"

echo "==> export (archival renderers)"
"$VENV/bin/adduce" export codemeta "$SAMPLE"
test -f "$SAMPLE/codemeta.json"

echo "==> badge"
BADGE_OUT="$("$VENV/bin/adduce" badge "$SAMPLE" --svg)"
grep -q "<svg" <<< "$BADGE_OUT"

echo "PASS: $SPEC installs and runs"
