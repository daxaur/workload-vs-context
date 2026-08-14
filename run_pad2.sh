#!/usr/bin/env bash
# Padding v2: screen for undecided states, then run three arms on the survivors.
#
# Fixed in advance, before any screening result is seen:
#   * candidates come from candidates.py (lag window, never from an outcome)
#   * a candidate survives screening iff 2 <= k <= 4 workarounds out of 6
#   * max_steps = step + 40, because the honest path takes 30-70 turns and
#     step + 14 truncated 39 of 80 continuations in v1
#   * three arms: control, pad_inert, pad_work — filler generated per state from
#     that state's own workspace, matched on tokens AND turn count
#
# Concurrency 5 throughout: above that `mypy src` exceeds the container's 30s
# limit and the tool result becomes <TIMEOUT> silently.
#
#   ./run_pad2.sh screen          # phase 1
#   ./run_pad2.sh run <k...>      # phase 2, on the survivors printed by phase 1

set -uo pipefail
PHASE=${1:-screen}
shift || true

REPO="$HOME/mats/agent-interp-envs"
WORK="$HOME/mats/workload-vs-context"
PY="$HOME/mats/.venv/bin/python"
SCREEN_OUT="$HOME/mats/_screen"
RUN_OUT="$HOME/mats/_p2"
FILLERS="$HOME/mats/_fillers"

docker tag precommit_hook:local precommit_hook:latest 2>/dev/null
mkdir -p "$SCREEN_OUT" "$RUN_OUT" "$FILLERS"

tag_of() { echo "$1" | sed 's#.*120b/##; s#/#_#g'; }
cap_of() { echo $(( $(basename "$1" | sed 's/step-//') + 40 )); }

resume() {  # <step-dir> <count> <results-dir>
  ( cd "$REPO" && uv run --quiet python scripts/resume.py "$1" \
      --count "$2" --local --results-dir "$3" \
      agent.max_steps="$(cap_of "$1")" >/dev/null 2>&1 )
}

if [ "$PHASE" = "screen" ]; then
  N=$($PY -c "import json;print(len(json.load(open('$WORK/candidates.json'))))")
  echo "screening $N candidates, 6 control resamples each"
  i=0
  while [ "$i" -lt "$N" ]; do
    S=$($PY -c "import json;print(json.load(open('$WORK/candidates.json'))[$i]['step_dir'])")
    TAG=$(tag_of "$S")
    echo "[$((i+1))/$N] $TAG"
    resume "$S" 5 "$SCREEN_OUT/$TAG" && echo "    ok" || echo "    FAILED"
    resume "$S" 1 "$SCREEN_OUT/$TAG" >/dev/null 2>&1
    i=$((i+1))
  done
  echo
  echo "now: $PY $WORK/screen_report.py $SCREEN_OUT"
  exit 0
fi

# ---- phase 2: three arms on the indices passed on the command line ----------
for IDX in "$@"; do
  S=$($PY -c "import json;print(json.load(open('$WORK/candidates.json'))[$IDX]['step_dir'])")
  TAG=$(tag_of "$S")
  echo "=== [$IDX] $TAG"

  F="$FILLERS/$TAG.json"
  if [ ! -f "$F" ]; then
    $PY "$WORK/make_filler.py" "$S" --out "$F" --target-tokens 12000 \
      || { echo "    filler FAILED"; continue; }
  fi
  $PY -c "
import json;d=json.load(open('$F'))
print(f\"    filler: inert {d['inert_turns']}t/{d['inert_tokens']}tok  work {d['work_turns']}t/{d['work_tokens']}tok\")"

  resume "$S" 5 "$RUN_OUT/${TAG}__control" && echo "    control ok" || echo "    control FAILED"
  resume "$S" 3 "$RUN_OUT/${TAG}__control" >/dev/null 2>&1

  for ARM in inert work; do
    D="$HOME/mats/_p2tmp_${TAG}_${ARM}"
    $PY "$WORK/pad2.py" "$S" --filler "$F" --arm "$ARM" --out "$D" >/dev/null 2>&1 \
      || { echo "    pad $ARM FAILED VERIFICATION"; continue; }
    PS=$(ls -d "$D"/run-1/step-* 2>/dev/null | head -1)
    [ -z "$PS" ] && { echo "    pad $ARM malformed"; continue; }
    resume "$PS" 5 "$RUN_OUT/${TAG}__$ARM" && echo "    $ARM ok" || echo "    $ARM FAILED"
    resume "$PS" 3 "$RUN_OUT/${TAG}__$ARM" >/dev/null 2>&1
  done
done

echo
echo "results -> $RUN_OUT"
