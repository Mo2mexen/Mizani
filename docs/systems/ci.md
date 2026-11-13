# CI (GitHub Actions)

Every push and pull request runs the same checks a developer runs locally, so `main` stays green.

## Where it lives
- `.github/workflows/ci.yml`

## How it works
On `ubuntu-latest`: install uv, `uv sync --locked` (fails if `uv.lock` is out of date with `pyproject.toml`), then ruff lint, ruff format check, pytest, `docker compose config` to catch a broken compose file, and the web page build (`npm ci && npm run build` in `web/`, Node 22), which also type-checks it.

## Running and testing it alone
The same steps on your machine:
```
uv sync --locked
uv run ruff check .
uv run ruff format --check .
uv run pytest -q
docker compose config --quiet
cd web && npm ci && npm run build
```

## Known limitations
- Tests never call the language model; model behaviour is covered by the eval runner, not CI.
- CI checks the compose file but does not start the containers, so the live-stack test (`tests/test_stack.py`) skips itself there. Run it by hand with the containers up after changing `docker-compose.yml` or `src/mizani/fhir.py` (README, "Develop and test"). A CI job that starts the three containers would add 5 to 10 minutes to every push.

## Extending it
Add a step to `ci.yml` and the same command to the list above.
