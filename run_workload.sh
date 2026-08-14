#!/usr/bin/env bash
# Workload dose-response, from step 0.
#
# The padding experiment varies CONTEXT with workload held byte-identical. This
# varies WORKLOAD from launch, with context free to move. Neither identifies
# alone; together they do:
#
#   padding arm      context up,  workload fixed   -> effect of context
#   this arm         workload up, context free     -> total effect
#
# `target_errors` is a launch-time key (entrypoint.py:35) — it selects a
# pre-built src_N variant. It cannot be changed mid-run, which is why this has
# to start from step 0 rather than resume a checkpoint.
#
# Concurrency 5 (mypy exceeds the container's 30s timeout above that, silently).
#
#   ./run_workload.sh <rollouts_per_condition>

set -uo pipefail
N=${1:-10}
REPO="$HOME/mats/agent-interp-envs"
OUT="$HOME/mats/_workload"

mkdir -p "$OUT"
docker tag precommit_hook:local precommit_hook:latest 2>/dev/null

for E in 0 51 258 602; do
  left=$N
  batch=0
  while [ "$left" -gt 0 ]; do
    take=5
    [ "$left" -lt 5 ] && take=$left
    batch=$((batch+1))
    echo "[errors=$E] batch $batch: $take rollouts"
    ( cd "$REPO" && uv run --quiet python scripts/run.py \
        --config "configs/precommit_hook/workload_${E}.yaml" \
        --count "$take" --local \
        --results-dir "$OUT/errors_${E}" >/dev/null 2>&1 ) \
      && echo "    done" || echo "    FAILED"
    left=$((left-take))
  done
done

echo
echo "results -> $OUT"
