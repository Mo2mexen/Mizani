# Mizani image (multi-arch: the base images exist for amd64 and arm64).
# Used by the mizani-mcp service and, with `command: ["mizani-api"]`, by the app service,
# which also serves the nurse's page built in the first stage.

# 1. The nurse's page (web/): a static build, served by the API.
FROM node:22-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web ./
RUN npm run build

# 2. The Python services.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.13 /uv /usr/local/bin/uv
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1 PATH="/app/.venv/bin:$PATH"

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project
COPY src ./src
RUN uv sync --locked --no-dev
COPY --from=web /web/dist ./web/dist

# Run as an unprivileged user; /data is created here so a new volume inherits its owner.
RUN useradd --create-home --uid 10001 mizani && mkdir -p /data && chown mizani /data
USER mizani

ENV MIZANI_DB=/data/mizani.db MIZANI_MCP_HOST=0.0.0.0 MIZANI_MCP_PORT=8000
EXPOSE 8000
CMD ["mizani-mcp"]
