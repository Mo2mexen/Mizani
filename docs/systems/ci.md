# CI (GitHub Actions)

Every push and pull request runs the same checks a developer runs locally, so `main` stays green.

## Where it lives
- `.github/workflows/ci.yml`

## How it works
On `ubuntu-latest`: install uv, `uv sync --locked` (fails if `uv.lock` is out of date with `pyproject.toml`), then ruff lint, ruff format check, pytest, and `docker compose config` to catch a broken compose file.

## Running and testing it alone
The same steps on your machine:
```
uv sync --locked
uv run ruff check .
uv run ruff format --check .
uv run pytest -q
docker compose config --quiet
```

## Known limitations
- Tests never call the language model; model behaviour is covered by the eval runner, not CI.
- CI checks the compose file but does not start the containers.

## Extending it
Add a step to `ci.yml` and the same command to the list above. The web UI build (`npm ci && npm run build` in `web/`) gets its own step once `web/` exists.
