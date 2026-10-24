#!/usr/bin/env bash
# Restart the API containers one at a time so the site stays up (Caddy routes around the one
# that is restarting). Use it for releases instead of `docker compose up -d` for the API:
#
#   docker compose --profile tls build --pull          # new images
#   docker compose run --rm migrate                    # migrations first (must be backward compatible)
#   deploy/rolling-restart.sh                          # api, then api-2
#   docker compose --profile tls up -d                 # workers, web, everything else
#
# Environment: COMPOSE="docker compose ..." (same as backup.sh); HEALTH_TIMEOUT (seconds, default 120).
set -euo pipefail

COMPOSE="${COMPOSE:-docker compose}"
TIMEOUT="${HEALTH_TIMEOUT:-120}"

wait_healthy() {
  local svc="$1" id status waited=0
  id="$($COMPOSE ps -q "$svc")"
  [ -n "$id" ] || { echo "[rolling] $svc has no container" >&2; return 1; }
  while :; do
    status="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$id")"
    [ "$status" = "healthy" ] && return 0
    if [ "$waited" -ge "$TIMEOUT" ]; then
      echo "[rolling] $svc is '$status' after ${TIMEOUT}s — stopping here; the other API container keeps serving." >&2
      $COMPOSE logs --tail 30 "$svc" >&2 || true
      return 1
    fi
    sleep 2
    waited=$((waited + 2))
  done
}

for svc in api api-2; do
  other=$([ "$svc" = api ] && echo api-2 || echo api)
  # Never take one down unless the other is healthy (otherwise this would cause an outage).
  wait_healthy "$other" || { echo "[rolling] $other is not healthy; refusing to restart $svc" >&2; exit 1; }
  echo "[rolling] recreating $svc"
  $COMPOSE up -d --no-deps --force-recreate "$svc"
  wait_healthy "$svc"
  echo "[rolling] $svc healthy"
  # Give Caddy's active health check (2 s interval) time to put it back into rotation.
  sleep 5
done
echo "[rolling] done: both API containers restarted without taking the site down"
