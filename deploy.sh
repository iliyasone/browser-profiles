#!/bin/sh
# Pull the latest main and rebuild. Meant to be the forced command of a
# deploy-only SSH key (see README, "Deploy from GitHub").
set -eu
cd "$(dirname "$0")"
git fetch -q origin main
git reset -q --hard origin/main
docker compose up -d --build --remove-orphans
docker image prune -f >/dev/null
echo "deployed $(git rev-parse --short HEAD)"
