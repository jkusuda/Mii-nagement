#!/usr/bin/env bash
# Deploy to the server: export the game for the web, copy it with the backend and the
# deploy files, and (re)start the containers.   deploy/push.sh root@<server ip>
set -euo pipefail
cd "$(dirname "$0")/.."

host="${1:?usage: deploy/push.sh root@<server ip>}"
[ -f deploy/server.env ] || { echo "Make deploy/server.env first (copy deploy/server.env.example)."; exit 1; }

# Real agents on a public server only behind a login (deploy/README.md, "Auth0").
setting() { sed -n "s/^$1=//p" deploy/server.env | tail -1; }
if [ "$(setting MOCK_AGENTS)" = "0" ] && { [ -z "$(setting AUTH0_DOMAIN)" ] || [ -z "$(setting AUTH0_AUDIENCE)" ] || [ -z "$(setting AUTH0_CLIENT_ID)" ]; }; then
  echo "MOCK_AGENTS=0 needs AUTH0_DOMAIN, AUTH0_AUDIENCE and AUTH0_CLIENT_ID in deploy/server.env."
  exit 1
fi
[ "$(setting PUBLIC_DEMO)" = "1" ] || { echo "Set PUBLIC_DEMO=1 in deploy/server.env."; exit 1; }

make web
rsync -azR --delete \
  --exclude .venv --exclude .worktrees --exclude __pycache__ --exclude '*.db' --exclude .secrets.json \
  backend deploy game/builds/web "$host:/opt/mii/"
ssh "$host" 'cd /opt/mii/deploy && docker compose --env-file server.env up -d --build && docker compose ps'
echo "Deployed: https://$(setting MII_DOMAIN)"
