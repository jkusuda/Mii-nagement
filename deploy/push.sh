#!/usr/bin/env bash
# Deploy to the server: copy the backend, the landing page and the deploy files (and the
# desktop app when built), and (re)start the containers.   deploy/push.sh root@<server ip>
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

# The browser build is off for now (the landing page only offers the download). To bring
# it back: `make web` here, add game/builds/web to the rsync, and uncomment the /app/
# handler in Caddyfile and the game volume in docker-compose.yml.
# make web
rsync -azR --delete \
  --exclude .venv --exclude .worktrees --exclude __pycache__ --exclude '*.db' --exclude .secrets.json \
  backend deploy "$host:/opt/mii/"
# The desktop app, when one was built (`make app`): the landing page's download button.
ssh "$host" 'mkdir -p /opt/mii/downloads'
if [ -f game/builds/macos/Mii-nagement.dmg ]; then
  rsync -az game/builds/macos/Mii-nagement.dmg "$host:/opt/mii/downloads/"
fi
ssh "$host" 'cd /opt/mii/deploy && docker compose --env-file server.env up -d --build && docker compose --env-file server.env ps'
echo "Deployed: https://$(setting MII_DOMAIN)"
