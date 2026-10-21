# Running the model on another machine

Mizani's agent reaches the language model through one OpenAI-compatible address, set by `LLM_BASE_URL` and `LLM_MODEL`. By default that address is Ollama on the same machine. You can point it at a stronger machine on your network instead: a desktop with a bigger GPU that several developers share, or one GPU server for every triage desk in a department. The code stays the same.

The default setup still runs everything on one machine with `docker compose up`. A remote model is optional.

## Speed

Each request and reply is a few kilobytes of text. On a local network that adds a few milliseconds per call. Across the internet, through a private network such as Tailscale, it adds tens of milliseconds. Generating an answer takes seconds, so the GPU on the model machine sets the speed. A desktop GPU with 8 GB or more runs `qwen3.5:9b-q4_K_M` faster than a 4 GB laptop GPU runs `qwen3.5:4b-q4_K_M`.

One GPU serves everyone who uses it. When several people send requests at once, or someone runs the eval suite, the others wait. Ollama's `OLLAMA_NUM_PARALLEL` setting controls how many requests it handles at the same time, and each extra slot needs more GPU memory.

## Same local network

On the model machine (Windows, admin PowerShell):

```powershell
setx OLLAMA_HOST "0.0.0.0:11434"
# Quit Ollama from the tray icon and start it again so it picks up the variable.
New-NetFirewallRule -DisplayName "Ollama" -Direction Inbound -Protocol TCP -LocalPort 11434 -Profile Private -Action Allow
ollama pull qwen3.5:9b-q4_K_M
ipconfig
```

`-Profile Private` opens the port only on networks Windows marks as private, never on public Wi-Fi. Note the IPv4 address that `ipconfig` prints. On macOS or Linux, set `OLLAMA_HOST` as described in the [Ollama FAQ](https://docs.ollama.com/faq).

On each machine that runs Mizani, in `.env`:

```
LLM_BASE_URL=http://<model-machine-ip>:11434/v1
LLM_MODEL=qwen3.5:9b-q4_K_M
```

To check the connection, `curl.exe http://<model-machine-ip>:11434/api/tags` should list the pulled models.

## Different networks

Install [Tailscale](https://tailscale.com/download) (free for personal use) on the model machine and on each machine that runs Mizani, and sign them in to the same tailnet. Use the model machine's Tailscale address (it starts with `100.`) in `LLM_BASE_URL`. If requests time out, check that the firewall rule above applies to the network profile Windows gave the Tailscale adapter.

Do not forward port 11434 on a router or expose it to the internet in any other way. Ollama has no authentication, so anyone who finds the port can use the GPU and read every prompt sent to it, which in Mizani includes complaint text.

## What stays the same

- Mizani must run fully on one machine. Keep `qwen3.5:4b-q4_K_M` pulled locally so work continues when the model machine is off.
- Record which model and which machine produced each eval result. A 9B model on a desktop GPU and a 4B model on a laptop give different numbers, and EVALS.md has to say which is which.
- Synthetic data only, whether the model is local or remote.
