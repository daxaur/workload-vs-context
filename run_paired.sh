#!/usr/bin/env bash
# Paired context-padding experiment.
#
# For each live checkpoint: resume it as-is (CONTROL) and resume a padded copy
# (PADDED) with identical filesystem, identical step budget, and a longer
# conversation. Same state, both arms — so between-state variance cancels.
#
# Concurrency is capped at 5: at --count 20 the container's 30s command timeout
# is exceeded by `mypy src`, which silently corrupts the batch (see CHECKS.md).
#
#   ./run_paired.sh <n_states> <resamples_per_arm> <pad_turns>

set -uo pipefail
N_STATES=${1:-6}
RESAMPLES=${2:-5}
PAD_TURNS=${3:-8}
CONC=5

REPO="$HOME/mats/agent-interp-envs"
WORK="$HOME/mats/workload-vs-context"
OUT="$HOME/mats/_paired"
PY="$HOME/mats/.venv/bin/python"

mkdir -p "$OUT"
docker tag precommit_hook:local precommit_hook:latest 2>/dev/null

STATES=()
while IFS= read -r line; do STATES+=("$line"); done < <("$PY" -c "import json;print(chr(10).join(json.load(open('/tmp/live_clean.json'))))")

echo "states available: ${#STATES[@]} · using $N_STATES · $RESAMPLES resamples/arm · pad $PAD_TURNS turns"
echo

i=0
for S in "${STATES[@]:0:$N_STATES}"; do
  i=$((i+1))
  TAG=$(echo "$S" | sed 's#.*120b/##; s#/#_#g')
  CAP=$(( $(basename "$S" | sed 's/step-//') + 14 ))

  echo "[$i/$N_STATES] $TAG  (resuming to max_steps=$CAP)"

  # CONTROL
  ( cd "$REPO" && uv run --quiet python scripts/resume.py "$S" \
      --count "$RESAMPLES" --local \
      --results-dir "$OUT/${TAG}__control" \
      agent.max_steps="$CAP" >/dev/null 2>&1 ) && echo "    control done" || echo "    control FAILED"

  # PADDED
  PADDIR="$HOME/mats/_padtmp_$i"
  "$PY" "$WORK/pad.py" "$S" --out "$PADDIR" --turns "$PAD_TURNS" >/dev/null 2>&1 || { echo "    pad FAILED"; continue; }
  ( cd "$REPO" && uv run --quiet python scripts/resume.py "$PADDIR" \
      --count "$RESAMPLES" --local \
      --results-dir "$OUT/${TAG}__padded" \
      agent.max_steps="$CAP" >/dev/null 2>&1 ) && echo "    padded  done" || echo "    padded  FAILED"
done

echo
echo "results → $OUT"
