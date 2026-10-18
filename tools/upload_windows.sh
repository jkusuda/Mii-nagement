#!/usr/bin/env bash
# Upload the Windows installer built here (`make windows`) as the website's
# "Download for Windows": https://<MII_DOMAIN>/download/Mii-nagement-Setup.exe.
#   tools/upload_windows.sh [root@<server ip>]   (default: DEPLOY_HOST, the server in deploy.yml)
# Needs SSH access to the server (your key in its /root/.ssh/authorized_keys).
set -euo pipefail
cd "$(dirname "$0")/.."

host="${1:-${DEPLOY_HOST:?usage: tools/upload_windows.sh root@<server ip>}}"
exe=game/builds/windows/Mii-nagement-Setup.exe
[ -f "$exe" ] || { echo "No $exe: run make windows first."; exit 1; }

# Copy under a temporary name, then rename: a download in progress never gets half a file.
echo "uploading $exe to $host"
ssh "$host" 'mkdir -p /opt/mii/downloads'
scp "$exe" "$host:/opt/mii/downloads/Mii-nagement-Setup.exe.new"
ssh "$host" 'mv /opt/mii/downloads/Mii-nagement-Setup.exe.new /opt/mii/downloads/Mii-nagement-Setup.exe'
echo "Uploaded: the website's Windows download is now this build."
