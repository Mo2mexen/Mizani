# Compose (running Mizani with one command)

`docker compose up` starts everything Mizani needs on Windows, Linux or an Apple Silicon Mac, with nothing else installed but Docker: the model server, our MCP server, the API with the agent and the web page, HTTPS in front of the page for tablets, the borrowed FHIR MCP server and a local FHIR store.

## Where it lives
- `docker-compose.yml`: the services.
- `docker/docker-compose.gpu.yml`: optional NVIDIA GPU override.
- `docker/Caddyfile`: the HTTPS proxy's configuration (service `caddy`).
- `docker/Dockerfile`: the one image built from this repo, and `docker/Dockerfile.dockerignore`, the list of what is left out of the build. The build context is still the repo root.
- `.env.example`: settings you can change by copying it to `.env`.

## How it works
1. `ollama` starts the model server (image `ollama/ollama:0.40.8`, multi-arch). Its port is not published, so it never clashes with an Ollama already installed on the machine; other services reach it as `http://ollama:11434`.
2. Once `ollama list` answers, `ollama-pull` downloads `LLM_MODEL` into the `ollama-models` volume and exits. The first run downloads 3.3 GB for the default model; later runs reuse the volume. When `LLM_BASE_URL` points at another model server (Ollama installed on the machine, a GPU machine on the network), `ollama-pull` downloads nothing and says so in its log, because that server already has the model.
3. `ollama-warm` then loads the model into memory and exits. Nothing waits for it, so the page starts meanwhile. Without it the first patient pays for reading the model from disk, which takes minutes on a hard disk. It is skipped, like the download, when `LLM_BASE_URL` points at another model server.
4. `mizani-mcp` (our MCP server, built from this repo) listens at `http://mizani-mcp:8000/mcp`, and at `127.0.0.1:8000` from this machine. Its health check asks `GET /health` every 10 s; it turns healthy once the server answers and can read the triage database.
5. `app` (command `mizani-api`) starts after the model is pulled and `mizani-mcp` and `fhir-mcp` are healthy: the API and the agent at `http://127.0.0.1:8090/api` (api.md; 8090 because 8080 is often taken). It shares the `mizani-data` volume with `mizani-mcp` so it can read the audit log, and keeps agent checkpoints in `/data/checkpoints.db`. It also serves the nurse's web page at `http://127.0.0.1:8090`, built from `web/` in the image's first stage (web.md). Its health check asks `GET /api/health` every 10 s (60 s grace at start).
6. `hapi-fhir` (HAPI FHIR v8.12.0-2, R4) is the local FHIR store, at `127.0.0.1:18080/fhir` from this machine (18080 because 8080 is often taken). Its H2 database lives in the `hapi-data` volume (mounted at the image user's home, `/home/nonroot`, so the volume gets that user as owner).
7. `fhir-mcp` (wso2 fhir-mcp-server, built from the pinned 0.10.0 commit) is the borrowed MCP server in front of HAPI, at `http://fhir-mcp:8000/mcp/` and `127.0.0.1:8001`. Auth is off because HAPI is local only. HAPI's image has no shell, so `fhir-mcp` carries the readiness check: it turns healthy once a FHIR search on HAPI answers, which also warms HAPI's database up, so the first patient's FHIR write after a cold start does not time out. Its request timeout is raised from 30 s to 120 s for slow disks.
8. `caddy` (image `caddy:2.11.9`) starts once `app` is healthy and serves the page and the API over HTTPS at `https://<MIZANI_HOSTNAME>:<HTTPS_HOST_PORT>` (default `https://localhost:8443`), proxying to `app:8090`. It listens on 127.0.0.1 unless `MIZANI_LISTEN` opens it (`0.0.0.0` for the hospital network). The certificate comes from Caddy's own authority (`MIZANI_TLS=internal`, the default; the authority lives in the `caddy-data` volume, and each device trusts it once) or from the hospital (`./certs` mounted read-only, `MIZANI_TLS=/certs/mizani.crt /certs/mizani.key`). It adds `X-Forwarded-Proto`, which `app` trusts (`FORWARDED_ALLOW_IPS`), so the session cookie is marked Secure. No compression, so the agent's live steps are not held back. Steps for hospital IT: docs/INSTALL.md.
9. `mizani-mcp`, `app` and `backup` share one image, `mizani:local`, built from `docker/Dockerfile`; `docker compose up --build` rebuilds it for all three. `backup` (profile `tools`) never starts with `docker compose up`. `docker compose run --rm backup` copies both data volumes into `./backups/<UTC time>/`, and `... backup restore <folder>` puts one back with the stack down (backup.md). It runs as root because HAPI's volume belongs to HAPI's user.

The server runs with an 8k context window, flash attention and a q8_0 KV cache, and `OLLAMA_KEEP_ALIVE=-1` keeps the model in memory for as long as the stack runs. Ollama's default unloads a model after 5 idle minutes, and the next patient then waits for it to load again (about two minutes on a laptop with a 4 GB GPU and a hard disk). The context size is set here because the OpenAI-compatible `/v1` API cannot set it per request, and at Ollama's 4k default a prompt with all tool definitions gets cut off without an error.

## Interfaces
| Name | Input | Output | Side effects |
|---|---|---|---|
| `LLM_MODEL` | Ollama model tag | model pulled and served | ~3.3 GB download on first run |
| `LLM_BASE_URL` | OpenAI-compatible URL | where the agent sends model calls | when it is not the compose `ollama` service, `ollama-pull` skips the download |
| `LLM_API_KEY` | key, empty by default | sent to a model server that checks keys | none |
| `MIZANI_SECOND_LOOK` | empty by default, `1` to turn on | the agent's second look at its chart signs (agent.md) | one more short model call per patient that has signs |
| `MIZANI_RULE_PACK` | pack name, default `sats_adult` | the triage chart `mizani-mcp` scores with (rules-engine.md) | an unknown or uncited pack stops `mizani-mcp` at start with the reason in its log, so the page never scores with a broken chart |
| `MIZANI_HOSTNAME` | a name or IP address, default `localhost` | the name tablets open over HTTPS (`caddy`'s certificate is for it); it also joins the app's allowed names | none |
| `MIZANI_LISTEN` | `127.0.0.1` (default) or `0.0.0.0` | where the HTTPS port listens: this machine only, or the network | opening it is the one step that makes Mizani reachable from tablets |
| `HTTPS_HOST_PORT` | port, default `8443` | the HTTPS port on the host (a hospital server uses 443) | none |
| `MIZANI_TLS` | `internal` (default) or `/certs/mizani.crt /certs/mizani.key` | Caddy's own certificate authority, or the hospital's certificate in `./certs` | none |
| `MIZANI_ALLOWED_HOSTS` | names, comma-separated; default `127.0.0.1,localhost` plus `MIZANI_HOSTNAME` | the names the page may be opened with (`app`); `mizani-mcp` has its own fixed list that adds its service name | any other `Host` is refused (DNS-rebinding guard, api.md); set it only to add more names |
| `ollama-warm` service | none | the model loaded into memory before the first patient | exits when done; skipped when `LLM_BASE_URL` points elsewhere |
| `ollama` service | HTTP on the compose network | model API at `http://ollama:11434` | stores models in `ollama-models` |
| `mizani-mcp` service | MCP over streamable HTTP | our tools at `http://mizani-mcp:8000/mcp` (this machine: `127.0.0.1:8000`) | writes the database in `mizani-data` |
| `caddy` service | HTTPS | the page and the API at `https://<MIZANI_HOSTNAME>:<HTTPS_HOST_PORT>` | keeps its certificate authority in `caddy-data` |
| `app` service | HTTP | the page and the API at `127.0.0.1:8090` | reads the audit log and keeps checkpoints in `mizani-data` |
| `hapi-fhir` service | FHIR REST | records at `127.0.0.1:18080/fhir` | stores them in `hapi-data` |
| `fhir-mcp` service | MCP over streamable HTTP | wso2's tools at `http://fhir-mcp:8000/mcp/` (this machine: `127.0.0.1:8001`) | writes to HAPI |

## Depends on / used by
- Depends on: Docker with Compose v2.
- Used by: everyone who runs Mizani; the agent in the `app` service reaches the model through `LLM_BASE_URL`.

## Safety rules this system enforces
- Everything runs locally; the model server is not exposed outside the compose network.
- Only HTTPS can be opened to the network, and only by `MIZANI_LISTEN`; the API's own port, the MCP servers and HAPI stay on 127.0.0.1 whatever `.env` says.
- `OLLAMA_NO_CLOUD=1` turns off Ollama's cloud models and web search, so no prompt (and no patient text) can leave the machine.

Data survives `docker compose down` and `up`: the waiting room, accounts, audit log and checkpoints in `mizani-data`, the FHIR records in `hapi-data`, the model in `ollama-models`, the HTTPS certificate authority in `caddy-data`. `docker compose down -v` deletes all four. Every published port is bound to 127.0.0.1 (HTTPS too, until `MIZANI_LISTEN` opens it), and each can be moved with `API_HOST_PORT`, `MCP_HOST_PORT`, `FHIR_MCP_HOST_PORT`, `HAPI_HOST_PORT` or `HTTPS_HOST_PORT` in `.env`.

## Running and testing it alone
```
docker compose up                                                          # CPU, any machine
docker compose -f docker-compose.yml -f docker/docker-compose.gpu.yml up   # NVIDIA GPU
```
With two compose files, Docker Desktop 4.96 on Windows stops the build with "additional privileges requested: pass --allow=fs.read=docker\\Dockerfile" (a Docker bug: one file alone builds). Setting `BUILDX_BAKE_ENTITLEMENTS_FS=0` once as a Windows user variable fixes it (README, "Running it").

To use an Ollama installed on the machine instead, set `LLM_BASE_URL=http://host.docker.internal:11434/v1` in `.env`. The compose `ollama` service still starts, idle and with no model in it. That Ollama is yours to configure: set `OLLAMA_KEEP_ALIVE=-1` in its environment if it should keep the model loaded too.

To empty the waiting room, the audit log and the FHIR records and keep the downloaded model: `docker compose down`, then `docker volume rm mizani_mizani-data mizani_hapi-data` (the names begin with the folder's name in lower case).

## Known limitations
- Docker on macOS has no GPU access, so on a Mac the model runs on CPU inside Docker, measured at 4 to 5 times slower than native Ollama on an M4. On a Mac, run Ollama natively and set `LLM_BASE_URL=http://host.docker.internal:11434/v1`.
- The GPU override needs the NVIDIA driver with WSL 2 (Windows) or the NVIDIA container toolkit (Linux).
- `docker-compose.yml` and `.env.example` stay in the repo root, because `docker compose up` looks for them there. The ignore file sits next to the Dockerfile as `Dockerfile.dockerignore`, which needs BuildKit (the default builder in Docker Desktop and in Docker Engine 23 or newer).

## Extending it
A new service goes in `docker-compose.yml`. The services built from this repo share one image (`docker/Dockerfile`) and differ by command: `mizani-mcp` is the image's default, and `app` runs `mizani-api`. Any new setting goes in `.env.example` with a working default in the compose file.
