# Website

The website and the backend on one server: a Vultr VM running two containers, the
backend (`backend.Dockerfile`) and Caddy (`Caddyfile`), which serves the landing page
(`landing/`, at `/`) and the desktop download (`/download/`), forwards the API and
WebSocket to the backend, and gets the HTTPS certificate.

The game itself is the desktop app (`make app`, see the README); the server only hosts
the page, the download, and a backend for HTTP clients.

## One-time setup

1. **Godot macOS export template** (for `make app`): Godot > Editor > Manage Export
   Templates > Download, or unzip `macos.zip` from the release's `.tpz` into
   `~/Library/Application Support/Godot/export_templates/4.7.2.stable/`.
2. **Auth0** (login; required when `MOCK_AGENTS=0`; skip it for a mock-mode server). The
   game has no login screen, so with login on it only sees public islands. In the Auth0
   dashboard:
   - Applications > APIs > Create API. Its *Identifier* (e.g. `https://mii-nagement/api`)
     is `AUTH0_AUDIENCE`. Signing algorithm RS256.
   - Applications > Create Application > *Native*. Its *Client ID* is `AUTH0_CLIENT_ID`,
     its *Domain* (`dev-xxxx.us.auth0.com`) is `AUTH0_DOMAIN`. Under Settings > Advanced
     Settings > Grant Types, turn on **Device Code**.
   - Keep strangers out: real agents run on your API keys. Authentication > Database >
     Username-Password-Authentication > **Disable Sign Ups**, add your team under User
     Management > Users, and on the application's Connections tab turn off the social
     logins (anyone with a Google account could otherwise log in).
3. **Vultr**: Account > API > enable the API and copy the key, then
   `VULTR_API_KEY=... python3 deploy/vultr.py`. It prints the server's address.
4. `cp deploy/server.env.example deploy/server.env` and fill it in: `MII_DOMAIN`, the
   Auth0 values, the API keys for the brains you use, and `GH_TOKEN`.

## Deploy (and redeploy after changes)

    make app                         # optional, on a Mac: the .dmg behind "Download for Mac"
    deploy/push.sh root@<server ip>

Then open `https://<MII_DOMAIN>`.

## On the server

    ssh root@<ip>
    cd /opt/mii/deploy
    docker compose logs -f backend     # the backend's log
    docker compose exec backend bash   # e.g. `gh auth status`, or `claude` to log in
    docker compose down                # stop (the data volume stays)

The database, the islands' repositories and the CLIs' logins live in the `data`
volume, so they survive a redeploy.

## Notes

- The .dmg isn't notarized: the landing page walks people through Open Anyway.
- `PUBLIC_DEMO=1` locks the Settings screen: settings and keys come from `server.env`.
