#!/usr/bin/env bash
# Deploy to the server: copy the backend, the landing page and the deploy files (and the
# desktop app when built), and (re)start the containers.   deploy/push.sh root@<server ip>
set -euo pipefail
cd "$(dirname "$0")/.."

host="${1:?usage: deploy/push.sh root@<server ip>}"

# The server keeps its own server.env. A local deploy/server.env (on your computer)
# replaces it; without one (GitHub Actions), the server's copy is used as it is.
env_file=deploy/server.env
if [ ! -f "$env_file" ]; then
  env_file=$(mktemp)
  trap 'rm -f "$env_file"' EXIT
  ssh "$host" 'cat /opt/mii/deploy/server.env' > "$env_file" \
    || { echo "No deploy/server.env here or on the server (copy deploy/server.env.example)."; exit 1; }
fi

# Real agents on a public server only behind a login (deploy/README.md, "Auth0").
setting() { sed -n "s/^$1=//p" "$env_file" | tail -1; }
if [ "$(setting MOCK_AGENTS)" = "0" ] && { [ -z "$(setting AUTH0_DOMAIN)" ] || [ -z "$(setting AUTH0_AUDIENCE)" ] || [ -z "$(setting AUTH0_CLIENT_ID)" ]; }; then
  echo "MOCK_AGENTS=0 needs AUTH0_DOMAIN, AUTH0_AUDIENCE and AUTH0_CLIENT_ID in server.env."
  exit 1
fi
[ "$(setting PUBLIC_DEMO)" = "1" ] || { echo "Set PUBLIC_DEMO=1 in server.env."; exit 1; }

rsync -azR --delete \
  --exclude .venv --exclude .worktrees --exclude __pycache__ --exclude '*.db' --exclude .secrets.json \
  --exclude server.env \
  backend deploy "$host:/opt/mii/"
[ ! -f deploy/server.env ] || rsync -az deploy/server.env "$host:/opt/mii/deploy/server.env"
# The desktop apps, when built (`make app`, `make windows`): the landing page's download buttons.
ssh "$host" 'mkdir -p /opt/mii/downloads'
for built in game/builds/macos/Mii-nagement.dmg game/builds/windows/Mii-nagement-Setup.exe; do
  if [ -f "$built" ]; then
    rsync -az "$built" "$host:/opt/mii/downloads/"
  fi
done
ssh "$host" 'cd /opt/mii/deploy && docker compose --env-file server.env up -d --build && docker compose --env-file server.env ps'
echo "Deployed: https://$(setting MII_DOMAIN)"
