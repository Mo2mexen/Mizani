"""HTTP API for the nurse's web page: intake, the live agent trace, the nurse's decision, the
queue, colours and the audit log. Shapes follow `schemas.py` and the team's API contract.

`POST /api/intake` starts one agent run and returns its id at once; the run's steps arrive on
`GET /api/triage/{id}/stream` (server-sent events). The run pauses at the nurse. Only
`POST /api/triage/{id}/decision` turns a named nurse's decision into an approval token, and the
token goes straight to the agent's commit step: the model never sees it.
"""

import asyncio
import logging
import os
import uuid
from collections.abc import AsyncIterable
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Annotated, Any

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.sse import EventSourceResponse, ServerSentEvent
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command
from pydantic import Field

from mizani import audit
from mizani.agent.graph import build_graph
from mizani.agent.mcp_tools import load_tools, read_json, server_url
from mizani.agent.model import chat_model
from mizani.approval import issue_approval
from mizani.db import db, db_path
from mizani.schemas import Contract, NurseDecision, Vitals

log = logging.getLogger("mizani.api")
FINAL_EVENTS = {"committed", "error"}
MCP_CONNECT_TRIES = 30  # the MCP server may still be starting (compose)


class Intake(Contract):
    patient_label: str = Field(min_length=1)
    complaint: str = Field(min_length=1)
    vitals: Vitals = Vitals()


class Remeasure(Contract):
    vitals: Vitals


class Run:
    """One patient's agent run as the browser sees it: every event so far, in order."""

    def __init__(self, events: list[tuple[str, Any]] | None = None):
        self.events = events or []
        self.changed = asyncio.Condition()
        self.lock = asyncio.Lock()  # one nurse answer at a time
        self.task: asyncio.Task | None = None

    async def emit(self, event: str, data: Any) -> None:
        async with self.changed:
            self.events.append((event, data))
            self.changed.notify_all()


async def _connect(server: Any) -> list:
    for attempt in range(MCP_CONNECT_TRIES):
        try:
            return await load_tools(server)
        except Exception:
            if attempt == MCP_CONNECT_TRIES - 1:
                raise
            log.warning("MCP server %s not reachable yet; retrying", server or server_url())
            await asyncio.sleep(1)
    return []


def create_app(llm=None, mcp_server: Any = None, checkpointer=None) -> FastAPI:
    """The API. Tests pass a fake model, the in-process MCP server and a memory checkpointer."""
    runs: dict[str, Run] = {}
    ctx: dict[str, Any] = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with AsyncExitStack() as stack:
            saver = checkpointer or await stack.enter_async_context(
                AsyncSqliteSaver.from_conn_string(str(db_path().with_name("checkpoints.db")))
            )
            tools = await _connect(mcp_server)
            rules = await read_json("rules://sats/adult", mcp_server)
            ctx["colours"] = await read_json("rules://sats/targets", mcp_server)
            ctx["graph"] = build_graph(
                llm or chat_model(), tools, [d["id"] for d in rules["discriminators"]], saver
            )
            yield

    app = FastAPI(title="Mizani API", lifespan=lifespan)

    def thread(run_id: str) -> dict:
        return {"configurable": {"thread_id": run_id}}

    async def drive(run: Run, payload: Any, run_id: str) -> None:
        """Run the agent until it pauses or ends, turning its updates into stream events."""
        try:
            async for chunk in ctx["graph"].astream(payload, thread(run_id), stream_mode="updates"):
                for node, update in chunk.items():
                    if node == "__interrupt__":
                        await run.emit("proposal", update[0].value)
                        continue
                    for step in (update or {}).get("trace", []):
                        await run.emit("step", step)
                    if (update or {}).get("committed"):
                        await run.emit("committed", update["committed"])
        except Exception as exc:
            log.exception("agent run %s failed", run_id)
            await run.emit("error", {"message": f"The agent stopped: {exc}"})

    async def get_run(run_id: str) -> Run:
        """The run, rebuilt from its checkpoint if the API restarted since it started."""
        if run_id in runs:
            return runs[run_id]
        state = await ctx["graph"].aget_state(thread(run_id))
        if not state.values:
            raise HTTPException(404, f"no triage run {run_id}")
        events: list[tuple[str, Any]] = [("step", s) for s in state.values.get("trace", [])]
        if state.interrupts:
            events.append(("proposal", state.interrupts[0].value))
        elif state.values.get("committed"):
            events.append(("committed", state.values["committed"]))
        runs[run_id] = Run(events)
        return runs[run_id]

    async def paused_proposal(run: Run, run_id: str) -> dict:
        state = await ctx["graph"].aget_state(thread(run_id))
        if (run.task and not run.task.done()) or not state.interrupts:
            raise HTTPException(409, "the agent is not waiting for a nurse on this run")
        return state.interrupts[0].value["proposal"]

    @app.post("/api/intake", status_code=202)
    async def intake(body: Intake) -> dict:
        run_id = uuid.uuid4().hex[:12]
        run = runs[run_id] = Run()
        entry = {
            "patient_label": body.patient_label,
            "complaint": body.complaint,
            "nurse_vitals": body.vitals.model_dump(exclude_none=True),
        }
        run.task = asyncio.create_task(drive(run, entry, run_id))
        return {"triage_id": run_id}

    @app.get("/api/triage/{run_id}/stream", response_class=EventSourceResponse)
    async def stream(
        run: Annotated[Run, Depends(get_run)],  # 404 before the stream starts
        last_event_id: Annotated[str | None, Header()] = None,
    ) -> AsyncIterable[ServerSentEvent]:
        sent = int(last_event_id) + 1 if last_event_id and last_event_id.isdigit() else 0
        while True:
            async with run.changed:
                await run.changed.wait_for(lambda n=sent: len(run.events) > n)
                batch = run.events[sent:]
            for event, data in batch:
                yield ServerSentEvent(event=event, data=data, id=str(sent))
                sent += 1
                if event in FINAL_EVENTS:
                    return

    @app.post("/api/triage/{run_id}/decision")
    async def decision(run_id: str, body: NurseDecision) -> dict:
        run = await get_run(run_id)
        async with run.lock:
            proposal = await paused_proposal(run, run_id)
            token = issue_approval(proposal["triage_id"], body)
            answer = {"action": body.action, "approval_token": token}
            await drive(run, Command(resume=answer), run_id)
            state = await ctx["graph"].aget_state(thread(run_id))
        if state.values.get("committed"):
            return state.values["committed"]
        raise HTTPException(409, state.values.get("nurse_error") or "the commit did not happen")

    @app.post("/api/triage/{run_id}/vitals", status_code=202)
    async def remeasure(run_id: str, body: Remeasure) -> dict:
        run = await get_run(run_id)
        async with run.lock:
            await paused_proposal(run, run_id)
            answer = {"action": "remeasure", "vitals": body.vitals.model_dump(exclude_none=True)}
            run.task = asyncio.create_task(drive(run, Command(resume=answer), run_id))
        return {"triage_id": run_id}

    @app.get("/api/colours")
    async def colours() -> list[dict]:
        return [
            {k: c[k] for k in ("colour", "label", "target_minutes", "source")}
            for c in ctx["colours"]
        ]

    @app.get("/api/queue")
    async def queue() -> list[dict]:
        rows = await read_json("queue://current", mcp_server)
        # Until the re-triage watch (W1) raises its own flags, a patient is due a re-check once
        # they have waited longer than the target time for their colour.
        return [{**r, "recheck_due": r["over_target"]} for r in rows]

    @app.get("/api/audit")
    async def audit_log(triage_id: str | None = None) -> list[dict]:
        return audit.entries(db(), triage_id)

    @app.get("/api/audit/verify")
    async def audit_verify() -> dict:
        broken = audit.verify_chain(db())
        return {"intact": broken is None, "first_broken_row": broken}

    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    uvicorn.run(
        create_app(),
        host=os.environ.get("MIZANI_API_HOST", "127.0.0.1"),
        port=int(os.environ.get("MIZANI_API_PORT", "8080")),
    )


if __name__ == "__main__":
    main()
