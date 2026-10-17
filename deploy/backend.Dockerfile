# The backend image for the web deployment (deploy/README.md). Build context: the repo root.
FROM node:22.20.0-bookworm-slim AS node

FROM python:3.12.12-slim-bookworm

# git for island repos, gh for GitHub, Node (npx) for the ACP agents and the gifts.
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && apt-get update && apt-get install -y --no-install-recommends git curl ca-certificates \
    && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg -o /usr/share/keyrings/githubcli.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/githubcli.gpg] https://cli.github.com/packages stable main" \
       > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update && apt-get install -y --no-install-recommends gh \
    && rm -rf /var/lib/apt/lists/*
# The CLIs the claude-code and gemini brains look for (codex comes through npx).
RUN npm install -g @anthropic-ai/claude-code @google/gemini-cli && npm cache clean --force
COPY --from=ghcr.io/astral-sh/uv:0.13.1 /uv /usr/local/bin/uv

# Agents never run as root. Everything that must survive a rebuild lives in /data:
# the database, the islands' repositories, their worktrees, and the CLIs' logins (HOME).
RUN useradd --create-home --home-dir /data/home mii && mkdir -p /app && chown mii /app /data
ENV HOME=/data/home \
    DATABASE_PATH=/data/mii.db \
    ISLANDS_ROOT=/data/islands \
    WORKTREE_ROOT=/data/worktrees \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    GIT_AUTHOR_NAME=Mii-nagement GIT_AUTHOR_EMAIL=mii@localhost \
    GIT_COMMITTER_NAME=Mii-nagement GIT_COMMITTER_EMAIL=mii@localhost
USER mii
WORKDIR /app/backend
COPY --chown=mii backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev
COPY --chown=mii backend/ ./

EXPOSE 8000
# Migrate, then serve. Caddy (deploy/Caddyfile) is in front and does HTTPS.
CMD ["sh", "-c", "uv run --no-dev python manage.py migrate --noinput && exec uv run --no-dev python -m uvicorn mii.asgi:application --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips '*'"]
