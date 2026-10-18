# Website

The landing page and the Mac download on one server: a Vultr VM running Caddy
(`Caddyfile`, `docker-compose.yml`), which serves the landing page (`landing/`, at `/`)
and the desktop download (`/download/`) and gets the HTTPS certificate.

There is no backend on the server: the game is the desktop app (`make app`, see the
README), which starts its own backend on the player's computer.

## One-time setup

1. **Godot macOS export template** (for `make app`): Godot > Editor > Manage Export
   Templates > Download, or unzip `macos.zip` from the release's `.tpz` into
   `~/Library/Application Support/Godot/export_templates/4.7.2.stable/`.
2. **Vultr**: Account > API > enable the API and copy the key, then
   `VULTR_API_KEY=... python3 deploy/vultr.py`. It prints the server's address.
3. `cp deploy/server.env.example deploy/server.env` and set `MII_DOMAIN`.

## Deploy (and redeploy after changes)

    make app                         # optional, on a Mac: the .dmg behind "Download for Mac"
    deploy/push.sh root@<server ip>

Then open `https://<MII_DOMAIN>`.

## Automatic deploys (GitHub Actions)

`.github/workflows/deploy.yml` deploys after CI passes on `main`: the landing page
(`push.sh`), then the Mac app (`make app` on a macOS runner, uploaded as the download).
Run it by hand from Actions > Deploy > Run workflow. It logs in with the repository
secret `DEPLOY_SSH_KEY` (its public half is in the server's `/root/.ssh/authorized_keys`,
comment `github-actions-deploy`) and uses the server's own `server.env`. Pull requests
never deploy.

## On the server

    ssh root@<ip>
    cd /opt/mii/deploy
    docker compose logs -f caddy       # Caddy's log
    docker compose down                # stop the site

## Notes

- The .dmg isn't notarized: the landing page walks people through Open Anyway.
- A server set up before the backend was dropped still has its leftovers: the next
  `push.sh` stops the old backend container (`--remove-orphans`); remove the rest by hand
  with `rm -rf /opt/mii/backend`, `docker volume rm deploy_data`, and delete the API keys
  and `GH_TOKEN` from `/opt/mii/deploy/server.env`.
