#!/bin/sh
# Pull the latest image built from main and restart. Run this on the pool device.
# Deliberately manual: never auto-update during a meet.
set -e
cd "$(dirname "$0")/.."
echo "==> pulling"
docker compose pull
echo "==> restarting"
docker compose up -d
echo "==> waiting for health"
for i in $(seq 1 20); do
  s=$(docker inspect -f '{{.State.Health.Status}}' meet-board 2>/dev/null || echo starting)
  [ "$s" = "healthy" ] && { echo "healthy"; docker compose ps; exit 0; }
  sleep 2
done
echo "did not become healthy — check: docker compose logs --tail=50"
exit 1
