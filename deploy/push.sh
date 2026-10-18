#!/usr/bin/env bash
# Deploy the website: copy the landing page and the deploy files (and the desktop app
# when built), and (re)start Caddy.   deploy/push.sh root@<server ip>
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
setting() { sed -n "s/^$1=//p" "$env_file" | tail -1; }
[ -n "$(setting MII_DOMAIN)" ] || { echo "Set MII_DOMAIN in server.env."; exit 1; }

rsync -azR --delete --exclude server.env deploy "$host:/opt/mii/"
[ ! -f deploy/server.env ] || rsync -az deploy/server.env "$host:/opt/mii/deploy/server.env"
# The desktop app, when one was built (`make app`): the landing page's download button.
ssh "$host" 'mkdir -p /opt/mii/downloads'
if [ -f game/builds/macos/Mii-nagement.dmg ]; then
  rsync -az game/builds/macos/Mii-nagement.dmg "$host:/opt/mii/downloads/"
fi
# --force-recreate: rsync swaps in a new Caddyfile, and a running container keeps reading
# the old one. --remove-orphans: stops containers no longer in docker-compose.yml.
ssh "$host" 'cd /opt/mii/deploy && docker compose --env-file server.env up -d --force-recreate --remove-orphans && docker compose --env-file server.env ps'
echo "Deployed: https://$(setting MII_DOMAIN)"
