#!/bin/bash

set -e

# Prefer the modern `docker compose` (v2, Go-based CLI plugin) over the
# deprecated `docker-compose` (v1, Python, EOL July 2023) — v1 has a known
# bug recreating an existing container against newer Docker Engine image
# metadata (`KeyError: 'ContainerConfig'`), which v2 doesn't have.
if docker compose version >/dev/null 2>&1; then
  COMPOSE="docker compose"
else
  COMPOSE="docker-compose"
  # v1's bug only ever hits a container that already exists — a fresh
  # create is unaffected. Removing (not the images/volumes, just the
  # containers) before `up` means every deploy is a fresh create instead of
  # a recreate, sidestepping the bug entirely rather than needing a manual
  # `docker rm -f` after every failed deploy.
  echo "docker-compose v1 detected — removing existing containers first (its recreate path is broken against this Docker Engine version)"
  $COMPOSE rm -sf 2>/dev/null || true
fi

echo "Using: $COMPOSE"

echo "Building images..."
$COMPOSE build

echo "Applying changes (recreates only the services whose image/config changed)..."
$COMPOSE up -d

echo "Running containers:"
$COMPOSE ps

echo ""
echo "translate logs:"
$COMPOSE logs --tail=50 translate
