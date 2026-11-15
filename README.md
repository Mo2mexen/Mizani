# Mizani

Mizani is an offline triage co-pilot for the front desk of a busy public hospital. A nurse types in a patient's vital signs and complaint, in English or Arabic. Mizani scores the patient on the South African Triage Scale (SATS), shows the exact rule behind every point, and suggests a priority colour. Nothing is saved until a named nurse confirms or overrides it. While patients wait, Mizani watches the queue and asks for fresh vitals when someone passes the target time for their colour.

*Mizani* means "scale" or "balance" in Swahili (*mizani*) and Arabic (*mīzān*).

> Built for the [African Agentic AI Design Challenge](https://agentic-africa-challenge.lovable.app/), Healthcare track ("The Triage Desk").
>
> **Demo video (2:32):** [youtu.be/5X2kgk-QHfY](https://youtu.be/5X2kgk-QHfY). One unedited take with the real model and its tool calls on screen, including where Mizani fails.
>
> **One command, on Windows, macOS or Linux:** `git clone https://github.com/Mo2mexen/Mizani.git`, `cd Mizani`, `docker compose up`, then open **http://127.0.0.1:8090**. Steps for each system: [Running it](#running-it).
>
> **It needs a local language model, and `docker compose up` provides one.** The first start downloads the model (Qwen3.5 4B, 3.3 GB) and the container images, about 7 GB in all; on our test machine it took 41 minutes on a 27 Mbit/s connection, nearly all of it downloading. After that Mizani runs offline, with no API keys or accounts, and starts in about 25 seconds. **Speed and chart-sign accuracy depend on the model and the machine:** a patient took 10 to 25 seconds with the model on a MacBook Air M4's GPU, and 1 to 2 minutes with the model inside Docker on its CPU. To use another model, copy `.env.example` to `.env` and set `LLM_MODEL`, `LLM_BASE_URL` and `LLM_REASONING_EFFORT` ([docs/REMOTE_MODEL.md](docs/REMOTE_MODEL.md)).

## Submission summary

**Track:** Healthcare, "The Triage Desk". **Sub-theme:** symptom-based priority scoring, with deterioration watch for patients who wait past their target time. **Facility and workflow:** the adult triage desk of a busy public hospital's outpatient or emergency department, where one or two nurses give every arriving patient a South African Triage Scale (SATS) colour.

Mizani helps that nurse without taking the decision away. The nurse types the vital signs and the complaint, in English or Arabic. An agent running on a local open-weights model (Qwen3.5 4B through Ollama) checks the readings, asks for a re-measure when one is missing or impossible, and then calls our MCP server to score the patient on SATS. A deterministic rules engine does the scoring from a rule pack that cites the adult chart for every value, so each point, matched sign and proposed colour shows the rule behind it.

The colour is only a proposal. A named nurse approves it or overrides it with a reason, and the MCP server refuses to save anything without that nurse's signed, single-use approval token, whatever the model does. Saved triages go onto a waiting-room board grouped by colour and into a local FHIR server through wso2's fhir-mcp-server, so other hospital systems can read them. While patients wait, the agent compares each wait with the SATS target time for the colour and raises a re-check flag for the nurse; it never moves a patient. Every tool call goes into a hash-chained audit log with inputs, outputs, time, approver, model, tokens, latency and cost.

Mizani never diagnoses or prescribes, treats patient text as data, and uses synthetic patients only. It runs offline on one machine with `docker compose up`. [EVALS.md](docs/EVALS.md) runs each synthetic task five times and reports the pass rate, the run-to-run variation, the cost per run and the failures we have not fixed. Speed and chart-sign accuracy depend on the model; a larger model is a configuration change, and the scoring, approval gate and audit log stay the same.

**Demo video:** [youtu.be/5X2kgk-QHfY](https://youtu.be/5X2kgk-QHfY) (2:32, one unedited take with the real model).

## The problem

In many public outpatient and emergency departments across Africa, one or two nurses sort every patient who walks in. They use a paper triage chart that turns vital signs and symptoms into a colour. When the queue is long, the chart is applied from memory and under pressure, and studies across the continent keep finding patients scored lower than their condition warrants: 24.4% of audited adult triages in three South African private emergency centres were under-triaged, and nurses in Beira, Mozambique assigned the correct SATS colour 34.2% of the time. An urgent patient can end up waiting behind a routine follow-up, and the hospital keeps no record of why a patient was ranked where they were.

The chart is sound: after one Egyptian university hospital adopted SATS, average emergency stays fell from about 184 to 51 minutes. The errors come from applying it by hand when the desk is busiest. Sources, settings and sample sizes: [docs/RESEARCH.md](docs/RESEARCH.md).

## What Mizani does at the desk

1. The nurse enters vitals (respiratory rate, pulse, blood pressure, temperature, oxygen saturation, level of consciousness, mobility, trauma) and a free-text complaint.
2. The agent extracts the facts, checks the vitals, and asks the nurse to re-measure any value that is missing or impossible.
3. A rules engine computes the Triage Early Warning Score (TEWS) and matches the complaint against the SATS discriminator list. The suggested colour is the higher of the two, and every point carries a citation.
4. The nurse confirms or overrides the suggestion with a reason. The MCP server refuses to save a triage without an approval from a named person.
5. The confirmed triage goes to the queue, the audit log and a local FHIR record.
6. While patients wait, Mizani compares their waiting time with the SATS target for their colour, raises a re-check flag when they are overdue, and re-scores with new vitals. Any change of colour goes back to the nurse.
7. On request, it drafts a three-line handover for the doctor: findings, what set the colour, and time waited.
8. When the patient goes in to be seen, the nurse records it under her name. The card leaves the waiting room and the record stays.

In shadow mode the nurse records their own colour before seeing Mizani's, and an agreement report shows where the two differ.

## What Mizani never does

- Diagnose a condition or suggest treatment. Its outputs are fixed fields: a colour, the rules that produced it, flags and a factual handover.
- Move a patient in the queue or change a colour without a named nurse's approval.
- Produce a priority without a source. When the language model and the rules disagree, the rules win and the case is flagged.
- Follow instructions written into a complaint field. Patient text is treated as data.
- Use real patient data or send anything off the machine. All development and testing use synthetic patients.

## How it works

```
Nurse UI (React): intake · queue board · approval dialog · agent trace · audit log
        │
FastAPI + LangGraph agent (pauses for the nurse, resumes after approval)
  ├─ Model: any OpenAI-compatible endpoint → Ollama + Qwen3.5 locally
  ├─ MCP ─► mizani-triage (our server): tools · resources · prompts
  └─ MCP ─► wso2 fhir-mcp-server ─► HAPI FHIR (local)
Audit log (SQLite, hash-chained): every tool call with inputs, outputs, time, approver, model, tokens, latency, cost
```

### Our MCP server

| Tool | Kind | Purpose |
|---|---|---|
| `validate_vitals` | read | Range, unit and completeness checks; returns re-measure requests |
| `compute_tews` | read | TEWS with a source for each component |
| `match_discriminators` | read | Maps extracted symptoms to SATS discriminators; only real chart entries are accepted |
| `propose_priority` | action | Creates a pending triage with colour and citations |
| `commit_triage` | action, needs approval | Saves the triage; requires an approval token and the approver's name |
| `raise_recheck_flag` | action | Flags an overdue or deteriorating patient for the nurse |
| `draft_handover` | action | Saves the doctor handover draft (findings only, up to 3 lines) |
| `release_handover` | action, needs approval | Releases the draft to the doctor; requires a nurse's handover approval token |
| `record_called_in` | action, needs approval | Records that a nurse called the patient in: the patient leaves the waiting list, the record stays |
| `record_nurse_assessment` | action | Stores the nurse's own colour for shadow mode |

Rule packs, target times, the live queue and each patient's timeline are exposed as MCP resources. The intake and handover instructions are MCP prompts.

### The borrowed MCP server

[wso2/fhir-mcp-server](https://github.com/wso2/fhir-mcp-server) connects the agent to a local [HAPI FHIR](https://hapifhir.io/) server. FHIR is the format hospital record systems use to exchange data, and using a maintained, tested FHIR MCP server saved us from writing our own FHIR client.

### Models

Scoring is done by deterministic rules, so the language model only reads free text, plans tool calls and writes explanations. That keeps a small open-weights model practical. The default is Qwen3.5 4B, which runs on a laptop with a 4 GB GPU or on a 16 GB Apple Silicon laptop. The same family goes up to 397B parameters, so a machine with more memory can run a larger model by changing `LLM_BASE_URL` and `LLM_MODEL`; nothing else changes. Speed and the accuracy of chart-sign matching depend on the model. The scoring, the approval gate and the audit log do not.

### Rule packs

Triage scales are YAML files, and each rule records its source. To add a scale (paediatric SATS, the WHO/ICRC/MSF Integrated Interagency Triage Tool, Manchester), you add a YAML file. The engine code stays the same.

The adult SATS pack encodes the 7 TEWS vitals with their scoring bands, the 4 colours with their target times, 39 chart signs (6 red, 23 orange, 10 yellow), the additional investigations and the plausible range of each vital, each with its source.

### When a case is not covered

- **The complaint matches no chart sign:** the colour comes from the vitals alone, and the proposal says so.
- **The model names a sign that is not on the chart, or one the complaint's words do not show:** the server refuses it and the proposal shows the flag.
- **A vital is missing or impossible:** Mizani asks for a re-measure and never fills it in.
- **A child, or a case outside the adult chart:** not covered yet (see Later).
- **In every case** the nurse decides and can override with a reason. Mizani never forces a colour.

## Evaluation

[EVALS.md](docs/EVALS.md) reports 39 synthetic tasks (23 written by hand, 16 messy variants), each run five times through the full agent. On Qwen3.5 4B on a MacBook Air M4, 180 of 195 runs passed (92%) on the current code, with no run-to-run variation, 9.7 s and 2,125 tokens per run on average, and $0 per run on the local model. With the optional second look on, the same 180 of 195 passed and no task changed, at 14.6 s and 2,871 tokens per run. A second model, Qwen3 4B Instruct 2507, passed 145 of 195 (74%) with nothing else changed. The safety gates held in every run of all three: no commit without a nurse's approval, impossible vitals never scored, missing vitals never made up, and hidden instructions in a complaint never set a sign. One failure is kept on purpose: a patient who falsely reports a fit gets a red proposal, because as text the report is identical to a true one; Mizani shows the sign with the patient's words, asks the nurse to confirm it and saves nothing until a named nurse decides. The other failure is the model naming the wrong chart sign while quoting real words from the complaint ("twisted my ankle" for a dislocation). The tasks cover correct colour and discriminator, recovery from missing or impossible vitals, re-triage timing, refusal to diagnose, instructions hidden in complaint text, attempts to commit without approval, a false claim and Arabic input; all six Arabic tasks pass on Qwen3.5 4B. Run them with `uv run mizani-evals --runs 5`.

## What clinicians told us

We put sixteen questions about triage at the desk, and about Mizani, to three clinicians in Egypt; the questions are in [docs/interview/questions.pdf](docs/interview/questions.pdf). Their handwritten Arabic answers, with a word-for-word English translation under each and a table of where they agree and differ, are in [docs/interview/answers.pdf](docs/interview/answers.pdf). They are:

- A doctor who works in a governmental hospital and in a charity hospital, and who worked as an emergency doctor during the COVID-19 pandemic. At her wish she is not named.
- Reham Mohamed Abdel-Aty, nursing specialist with 16 years of experience at Al-Khazindara General Hospital in Shubra (governmental), and Heba Rizk, nurse with 12 years of experience at St. Teresa Charity Hospital. They answered on one shared sheet.

The desks they describe differ. At the doctor's, patients are seen in arrival order, a patient may wait an hour before anyone assesses them, and waiting patients are not checked again. At the nurses', a doctor sets the order by the vital signs, a patient waits about 10 minutes, and a nurse re-checks waiting patients several times. All three find a suggested colour with its reason, a re-check reminder and the three-line handover (the vital signs, the reason for the colour, the waiting time) useful.

On a patient who says "I am having a seizure right now", they split. The doctor does not accept a colour that rests on the patient's words alone: the real signs must be taken first. The nurses call Mizani's behaviour correct: listen to the complaint and assess the case. Both answers put the decision with the person who sees the patient. Because of the doctor's answer we changed the page: such a proposal now reads "RED if confirmed", with what the vitals alone score, until the nurse ticks that she checked the sign ([Known limitations](#known-limitations)).

They also split on the language of the screen: the doctor said an Arabic screen is not required, the nurses said it should be in Arabic. Mizani reads complaints in Arabic; its page is in English ([Later](#later)).
## Running it

**What you need.** Docker: Docker Desktop on Windows (with WSL 2) or macOS, Docker Engine with the Compose plugin on Linux. On Windows and macOS, start Docker Desktop first: `docker compose up` talks to it and does not start it. Give Docker at least 8 GB of memory: on our test machine the whole stack peaked at 5.5 GB while the model answered. About 16 GB of free disk for the images, the model and the data. Internet for the first start only. Ports 8090, 8000, 8001 and 18080 on 127.0.0.1 (each can be changed, see below).

**Speed.** The rules, MCP tools and audit log take a few seconds per patient; the rest is the model. Measured on a MacBook Air M4 (16 GB): 1 to 2 minutes per patient with the model inside Docker (Docker on macOS has no GPU access), 10 to 25 seconds with Ollama installed on the Mac, and about 10 seconds in the evaluation runs. The model is loaded into memory in the background as the stack starts, which takes seconds from an SSD and a few minutes from a hard disk; a patient entered before that has finished waits for it, and the page says so. On a Mac, for speed, install Ollama, run `ollama pull qwen3.5:4b-q4_K_M`, and put `LLM_BASE_URL=http://host.docker.internal:11434/v1` in `.env`; `docker compose up` then skips its own model download. The model server inside Docker keeps the model in memory between patients; an Ollama you installed yourself unloads it after 5 idle minutes unless you set `OLLAMA_KEEP_ALIVE=-1` for it. On Windows or Linux with an NVIDIA GPU, add the GPU file: `docker compose -f docker-compose.yml -f docker/docker-compose.gpu.yml up`. For better chart-sign choices, use a larger model on a stronger machine: set `LLM_MODEL` (and `LLM_BASE_URL` if it runs elsewhere) in `.env`; [docs/REMOTE_MODEL.md](docs/REMOTE_MODEL.md) shows how to use a GPU machine on your network. We evaluated only the 4B model; the safety checks do not depend on which model runs.

**macOS or Linux**
```
git clone https://github.com/Mo2mexen/Mizani.git
cd Mizani
docker compose up
```
**macOS, faster (optional).** Docker on macOS cannot use the Mac's GPU, so the model inside Docker runs on the CPU. To use the GPU, let the Ollama app serve the model:
1. Install [Ollama](https://ollama.com/download) and run `ollama pull qwen3.5:4b-q4_K_M`.
2. Create a file named `.env` next to `docker-compose.yml` with one line: `LLM_BASE_URL=http://host.docker.internal:11434/v1`.
3. To keep the model in memory between patients, run `launchctl setenv OLLAMA_KEEP_ALIVE -1` and restart the Ollama app (its default unloads the model after 5 idle minutes).
4. With the Ollama app open, run `docker compose up`. Its log says "nothing to pull" and "nothing to load", because the app already has the model. `docker compose down` stops Mizani; the Ollama app is quit from the menu bar.

**Windows** (PowerShell)
1. Install [Docker Desktop](https://www.docker.com/products/docker-desktop/) with the WSL 2 backend (the default; if it asks for WSL, run `wsl --install` in an administrator PowerShell and restart) and [Git for Windows](https://git-scm.com/download/win). Start Docker Desktop.
2. Memory: WSL 2 gives Docker half of your RAM, so 8 GB on a 16 GB laptop is enough. With less RAM, create `%UserProfile%\.wslconfig` containing the two lines `[wsl2]` and `memory=8GB`, run `wsl --shutdown`, and start Docker Desktop again.
3. In PowerShell:
```
git clone https://github.com/Mo2mexen/Mizani.git
cd Mizani
docker compose up
```
The repository forces LF line endings (`.gitattributes`), so a Windows checkout builds the same Linux images.
Then open **http://127.0.0.1:8090** once the log shows `Uvicorn running on http://0.0.0.0:8090`. The page has the intake, the agent's steps live, the proposal with the rule behind every number, approval, the waiting room and the audit log ([docs/systems/web.md](docs/systems/web.md)).

`docker compose up` starts the model server (Ollama, pulling the model once and loading it into memory while the rest starts), our MCP server at `http://127.0.0.1:8000/mcp` (try it by hand with `npx @modelcontextprotocol/inspector`), the API with the agent at `http://127.0.0.1:8090` (try it from `http://127.0.0.1:8090/docs`, or with curl as in [docs/systems/api.md](docs/systems/api.md)), wso2's FHIR MCP server at `http://127.0.0.1:8001/mcp/` and a local HAPI FHIR store at `http://127.0.0.1:18080/fhir`. The API waits until HAPI answers, so the first patient's FHIR record is written too.

- **Stop:** Ctrl+C, or `docker compose down`. The waiting room, the audit log, the FHIR records and the model are kept in Docker volumes, so the next `docker compose up` starts where you left off: a restart or a power cut must not lose the patients who are waiting or the audit log. `docker compose down -v` deletes all of it for a clean start, the model included. To empty only the waiting room, the audit log and the FHIR records and keep the model, run `docker compose down` and then `docker volume rm mizani_mizani-data mizani_hapi-data` (the names begin with the folder's name in lower case).
- **After `git pull`:** run `docker compose up --build`, so every image is rebuilt from the new code. Plain `up` reuses the images built before, and an old MCP server with a new agent refuses its calls.
- **A port is taken:** copy `.env.example` to `.env` and set `API_HOST_PORT`, `MCP_HOST_PORT`, `FHIR_MCP_HOST_PORT` or `HAPI_HOST_PORT` (the page is then at `http://127.0.0.1:<API_HOST_PORT>`). Every port listens on this machine only.
- **Everything else you might install** (for running and for development) is listed in [packages.txt](packages.txt). How the parts fit: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Connect your own agent or system

The MCP server works without our agent, so another agent can use the triage tools as they are. With the stack running (or only `docker compose up mizani-mcp`), point any MCP client at `http://127.0.0.1:8000/mcp`:

```
npx @modelcontextprotocol/inspector                                        # call the tools by hand: Streamable HTTP, that address
claude mcp add --transport http mizani-triage http://127.0.0.1:8000/mcp    # or add it to an MCP client, here Claude Code
```

An agent can check vitals, compute the TEWS with a source for every point and propose a priority. It cannot save one: `commit_triage` refuses any call without an approval token signed for a named nurse, and that refusal is logged too.

[docs/INTEGRATION.md](docs/INTEGRATION.md) has a Python example with its output, the approval step for a front end of your own, the HTTP calls that send a patient through Mizani's agent, and how a record system reads the FHIR records.

## Develop and test

You need Python 3.12, [uv](https://docs.astral.sh/uv/) and Docker (see [packages.txt](packages.txt)).

```
uv sync                          # install dependencies from uv.lock
uv run pytest                    # all tests; none of them need a language model
uv run ruff check . && uv run ruff format --check .
MIZANI_APPROVAL_SECRET=dev uv run mizani-mcp      # MCP server at http://127.0.0.1:8000/mcp
npx @modelcontextprotocol/inspector              # call its tools by hand in the browser
docker compose up -d mizani-mcp fhir-mcp hapi-fhir
MIZANI_E2E=1 uv run pytest tests/test_stack.py   # proposal -> approval -> commit -> FHIR, no model
MIZANI_APPROVAL_SECRET=dev MIZANI_FHIR_MCP_URL=http://127.0.0.1:8001/mcp/ uv run mizani-api   # API + agent at http://127.0.0.1:8090
uv run mizani-evals --help                      # the evaluation runner (EVALS.md)
cd web && npm ci && npm run dev                  # the page at http://localhost:5173 against that API (Node 22)
VITE_USE_MOCK=true npm run dev                   # the page on fake data, no backend
```

Plain `uv run pytest` reports one skipped test. It is `tests/test_stack.py`, the only test that needs real containers (our MCP server, wso2's server and HAPI FHIR), and it runs when `MIZANI_E2E=1` is set, as in the two lines above. CI does not start containers, so run it by hand after changing `docker-compose.yml`, `docker/Dockerfile` or `src/mizani/fhir.py`. The test signs its approval with the same secret compose gives the server: the shell's `MIZANI_APPROVAL_SECRET`, else the one in `.env`, else the compose default.

On Windows PowerShell, set the secret with `$env:MIZANI_APPROVAL_SECRET="dev"` first. On a Mac, run Ollama natively rather than in Docker (Docker on macOS has no GPU, and the model runs 4 to 5 times slower there) and set `LLM_BASE_URL=http://host.docker.internal:11434/v1` in `.env`. How each part works is in [docs/systems/](docs/systems/).

## What works today

| Part | State |
|---|---|
| Rules engine (SATS adult), every rule cited | done, tested |
| MCP server `mizani-triage`: 10 tools, 5 resources, 2 prompts, approval gate, hash-chained audit; a chart sign counts only with the complaint's own words behind it, never from text addressed to the software; a red colour from the complaint alone needs the nurse's confirmation | done, tested through an MCP client |
| Agent (LangGraph): reads the entry, checks vitals, scores through the MCP tools, pauses for the nurse, commits with the nurse's token; retries and falls back to the nurse's own vitals when the model's tool calls are wrong or the model server does not answer, and the proposal then says the chart signs were not checked; model, tokens and cost logged with every call | done, tested with a fake model and live with Qwen3.5 4B |
| Second look (optional, `MIZANI_SECOND_LOOK=1`): the model sees what the server made of its chart signs and corrects or withdraws them once; a withdrawn sign is flagged for the nurse, and an emergency (red) sign cannot be withdrawn | built, tested with a fake model; evaluated on Qwen3.5 4B (180/195 runs pass with it on and with it off, slower with it on); off by default |
| API (FastAPI): intake, live agent steps as server-sent events, nurse decision (approval token), re-measure, re-triage, handover draft and release, called in, shadow-mode assessment and report, queue, colours, audit log with chain check | done, tested, run live |
| Web page (React + Vite): intake, the proposal with a source on every number and the words behind every sign, nurse decision with name, re-measure, shadow mode, waiting room with re-check, handover, called in and each patient's recorded details, the agent's steps live (a section the nurse can hide), audit log with the chain check, waiting-room search; desk, tablet and phone layouts, keyboard and screen-reader use | done; served by the API at http://127.0.0.1:8090; every scenario tested in a browser on the full stack |
| FHIR output: the agent's commit step writes Patient, Encounter and Observations through wso2 fhir-mcp-server to a local HAPI FHIR | done, verified end to end and in a live run |
| Re-triage watch: target-time timers; the agent raises a re-check flag every 30 s for patients past their target time; re-triage with fresh vitals as a new pending proposal that replaces the old entry once a nurse commits it | done, tested, run live |
| Handover (the model drafts two lines of findings, the waiting time comes from the record, every number is checked against the record, a named nurse releases that exact draft) and shadow mode (nurse's own colour, agreement report) | done, tested, run live |
| Evaluation suite: 39 tasks × 5 runs with pass rate, run-to-run variation, latency, tokens, cost | done on 13 Nov 2026: Qwen3.5 4B 180/195 runs pass (92%), the same with the second look on; Qwen3 4B Instruct 2507 145/195 (74%); failures documented |

## Known limitations

- **A patient's own false report of a chart sign cannot be caught from the text.** A calm patient with a rash who says "I am having a fit right now" gets a red proposal (EVALS.md, `false-claim-01`). No model can fix this: the words are the patient's history, not an instruction, and as text a false report is identical to a true one; only the nurse examining the patient can tell. Mizani shows the sign with the patient's words, shows the colour as "RED if confirmed" with what the vitals alone score, blocks Approve until the nurse ticks that the sign was checked with the patient (the server refuses it too), and saves nothing until a named nurse approves or overrides.
- The model picks the chart signs from the complaint. A sign counts only with the complaint's own words behind it, but the words can be real and still not mean the sign: on Qwen3.5 4B, "twisted my ankle" was quoted for a dislocation (orange instead of green); 15 of 195 evaluation runs failed this way or on the false claim (EVALS.md). The second model we evaluated, Qwen3 4B Instruct 2507, made more such mistakes, among them "vomited once" read as persistent vomiting and a stroke described in Arabic given a dislocation. The vital-sign score comes from the rules alone, so the model can raise a colour through a sign but cannot lower the score the vitals give, and the nurse sees every sign with its words and source.
- Instructions hidden in a complaint are recognised by a list of English and Arabic patterns and shown to the nurse; a sign they name, or quote from them, is ignored. An instruction phrased another way is not recognised as one, but any sign it asks for still needs the complaint's own words outside it, and a red colour from words alone still goes to the nurse to confirm.
- Mizani is a prototype tested on synthetic patients only. It has not been validated in a clinic and is not a medical device.
- Only the adult SATS chart is encoded. Children, pregnancy and the paediatric chart are out of scope for now.
- There is no login. The nurse types a name, and the API trusts anyone who can reach it, so every port listens on 127.0.0.1 only. A hospital deployment would need accounts behind the approval step. The API and the MCP server also answer only to the names `127.0.0.1` and `localhost`, so a web page on another site cannot reach them through the nurse's browser. The default approval secret is public: change `MIZANI_APPROVAL_SECRET` in `.env` on any shared machine.
- The audit log's hash chain shows any row changed or removed in the middle, but someone who can write the database file could rewrite the whole chain from the start, or drop the last rows. Keeping a copy of the latest hash on another machine, with each backup, would show that; it is not built yet.
- One desk, one SQLite file. The page is in English; complaints can be typed in Arabic or English.
- A patient leaves the waiting room when a nurse records that they were called in. After that there is no handover draft or re-check for them, and the FHIR Encounter is not updated yet.
- Speed depends on the machine and the model (see [Running it](#running-it)). The model is loaded into memory in the background as the stack starts, which takes seconds from an SSD and a few minutes from a hard disk; a patient entered before that has finished waits for it, and the page says so.
- The handover's two lines of findings are written by the model, so a nurse must read them before release. The API tidies the draft (two lines, no bullets, no waiting time of the model's own), every number in it is checked against the patient's record, and the waiting time comes from the record; a number written in words is not checked, and a weaker model writes clumsier sentences.

## Later

- **A ward deployment.** Today one machine runs everything and only that machine can open the page. In a hospital, one server (a PC or small server in the department, with a GPU for a larger model) would run the stack, and nurses would open the page from any browser on the hospital network, at the desk or on a tablet. That needs nurse accounts and a login behind the approval step first, the approval secret set per site, and the server's address added to `MIZANI_ALLOWED_HOSTS`.
- **More desks and hospitals.** SQLite serves one desk. Several desks or hospitals need a shared database (Postgres) and accounts per facility.
- **A larger model.** A bigger open-weights model on a GPU server is a configuration change (`LLM_MODEL`, `LLM_BASE_URL`); speed and chart-sign accuracy should improve, and EVALS.md would be re-run for it.
- **More rule packs.** Paediatric SATS, the WHO/ICRC/MSF triage tool and Manchester, each as a YAML file.
- **Arabic interface.** Complaints can already be typed in Arabic; the page itself is English. Both nurses we interviewed asked for an Arabic screen.
- **And:** voice input for complaints, WhatsApp check-in before arrival, Bluetooth pulse oximeters, daily counts to DHIS2, a training mode for new nurses built on shadow mode.

## Third-party components

| Component | Licence |
|---|---|
| MCP Python SDK | MIT |
| LangGraph, LangChain (langchain-core, langchain-openai) | MIT |
| FastAPI | MIT |
| Pydantic | MIT |
| PyYAML | MIT |
| React, Vite | MIT |
| Ollama | MIT |
| Qwen3.5 open-weights models | Apache-2.0 |
| wso2 fhir-mcp-server | Apache-2.0 |
| HAPI FHIR | Apache-2.0 |
| SQLite | Public domain |

The South African Triage Scale is published by the Emergency Medicine Society of South Africa. Mizani encodes its rules with citations and does not reproduce the SATS manual. Every source we rely on, clinical and statistical, is listed in [docs/RESEARCH.md](docs/RESEARCH.md).

## Team

Built by two students at the Arab Academy for Science, Technology and Maritime Transport (AAST), Egypt: [@Mo2mexen](https://github.com/Mo2mexen) and [@JohnAtef907](https://github.com/JohnAtef907).

## Licence

[Apache-2.0](LICENSE)
