#!/usr/bin/env bash
# Queue saturation with 1, 2 and 4 orchestrator processes (same burst each time).
# Prereqs: perf API on :8100 with the perf database and `loadtest.py setup` done (docs/PERFORMANCE.md).
#   PERF_ENV=<file with the perf environment exports> perf/orchestrator_scaling.sh [tasks]
# Writes perf/results/orchestrators-<N>.json. Never run against production.
set -euo pipefail
cd "$(dirname "$0")/.."
TASKS="${1:-300}"
: "${PERF_ENV:?set PERF_ENV to the perf environment file}"
# shellcheck disable=SC1090
source "$PERF_ENV"

for n in 1 2 4; do
  pids=()
  (cd api && exec uv run python -m app.workers tools >"var/perf-tools-$n.log" 2>&1) &
  pids+=($!)
  for i in $(seq 1 "$n"); do
    (cd api && exec uv run python -m app.workers orchestrator >"var/perf-orch-$n-$i.log" 2>&1) &
    pids+=($!)
  done
  sleep 6  # workers register and start polling
  echo "== $n orchestrator(s)"
  uv run --project api python perf/loadtest.py queue --tasks "$TASKS" --db "${PERF_READ_DB_URL:-$DATABASE_URL}" 2>/dev/null | tail -1 | tee "perf/results/orchestrators-${RESULT_TAG:-}$n.json"
  kill -TERM "${pids[@]}" 2>/dev/null || true
  wait "${pids[@]}" 2>/dev/null || true
  sleep 3
done
