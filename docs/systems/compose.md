# Compose (running Mizani with one command)

`docker compose up` starts everything Mizani needs on Windows, Linux or an Apple Silicon Mac, with nothing else installed but Docker: the model server, our MCP server, the borrowed FHIR MCP server and a local FHIR store. The app (agent, API, UI) joins it when it is built.

## Where it lives
- `docker-compose.yml`: the services.
- `docker-compose.gpu.yml`: optional NVIDIA GPU override.
- `.env.example`: settings you can change by copying it to `.env`.

## How it works
1. `ollama` starts the model server (image `ollama/ollama:0.40.8`, multi-arch). Its port is not published, so it never clashes with an Ollama already installed on the machine; other services reach it as `http://ollama:11434`.
2. Once `ollama list` answers, `ollama-pull` downloads `LLM_MODEL` into the `ollama-models` volume and exits. The first run downloads 3.3 GB for the default model; later runs reuse the volume.
3. `mizani-mcp` (our MCP server, built from this repo) listens at `http://mizani-mcp:8000/mcp`, and at `127.0.0.1:8000` from this machine.
4. `hapi-fhir` (HAPI FHIR v8.12.0-2, R4) is the local FHIR store, at `127.0.0.1:18080/fhir` from this machine (18080 because 8080 is often taken).
5. `fhir-mcp` (wso2 fhir-mcp-server, built from the pinned 0.10.0 commit) is the borrowed MCP server in front of HAPI, at `http://fhir-mcp:8000/mcp/` and `127.0.0.1:8001`. Auth is off because HAPI is local only.

The server runs with an 8k context window, flash attention and a q8_0 KV cache. The context size is set here because the OpenAI-compatible `/v1` API cannot set it per request, and at Ollama's 4k default a prompt with all tool definitions gets cut off without an error.

## Interfaces
| Name | Input | Output | Side effects |
|---|---|---|---|
| `LLM_MODEL` | Ollama model tag | model pulled and served | ~3.3 GB download on first run |
| `LLM_BASE_URL` | OpenAI-compatible URL | where the agent sends model calls | none |
| `ollama` service | HTTP on the compose network | model API at `http://ollama:11434` | stores models in `ollama-models` |

## Depends on / used by
- Depends on: Docker with Compose v2.
- Used by: the agent (via `LLM_BASE_URL`), and later the MCP server and app services.

## Safety rules this system enforces
- Everything runs locally; the model server is not exposed outside the compose network.
- `OLLAMA_NO_CLOUD=1` turns off Ollama's cloud models and web search, so no prompt (and no patient text) can leave the machine.

## Running and testing it alone
```
docker compose up                                                   # CPU, any machine
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up   # NVIDIA GPU
```
To use an Ollama installed on the machine instead, set `LLM_BASE_URL=http://host.docker.internal:11434/v1` in `.env`.

## Known limitations
- Docker on macOS has no GPU access, so on a Mac the model runs on CPU inside Docker, measured at 4 to 5 times slower than native Ollama on an M4. On a Mac, run Ollama natively and set `LLM_BASE_URL=http://host.docker.internal:11434/v1`.
- The GPU override needs the NVIDIA driver with WSL 2 (Windows) or the NVIDIA container toolkit (Linux).

## Extending it
New services (`mizani-mcp` in M1, `app` in U1) go in `docker-compose.yml`, read `LLM_BASE_URL` and `LLM_MODEL` from the environment, and get their own Dockerfile. Any new setting goes in `.env.example` with a working default in the compose file.
