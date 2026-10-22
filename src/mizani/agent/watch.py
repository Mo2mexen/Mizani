"""Re-triage watch, agent side: flag waiting patients whose SATS target time has passed.

The MCP server keeps the timers (`queue://current`) and refuses a flag that is not due; this step
only asks, every WATCH_SECONDS. It never changes a colour or the queue. A re-triage is a new run
with the nurse's fresh vitals and `retriage_of`, approved by a nurse like any other triage.
"""

import asyncio
import logging
from typing import Any

from langchain_core.tools import BaseTool

from mizani.agent.mcp_tools import read_json

WATCH_SECONDS = 30
log = logging.getLogger("mizani.watch")


async def raise_due_rechecks(tools: dict[str, BaseTool], server: Any = None) -> list[dict]:
    """Flag every waiting patient who is due a re-check and not flagged yet. Returns the flags."""
    raised = []
    for entry in await read_json("queue://current", server):
        if not entry["recheck_due"] or entry["recheck_raised"]:
            continue
        call = {
            "name": "raise_recheck_flag",
            "args": {"triage_id": entry["triage_id"]},
            "id": f"watch-{entry['triage_id']}",
            "type": "tool_call",
        }
        message = await tools["raise_recheck_flag"].ainvoke(call)
        if message.status == "error":  # e.g. re-triaged meanwhile; the server said why
            log.warning("re-check for %s not raised: %s", entry["triage_id"], message.content)
            continue
        raised.append({"triage_id": entry["triage_id"], **message.artifact})
    return raised


async def watch(tools: dict[str, BaseTool], server: Any = None) -> None:
    """Run `raise_due_rechecks` forever. A failed round is logged and tried again."""
    while True:
        try:
            for flag in await raise_due_rechecks(tools, server):
                log.info("re-check due: %s", flag["message"])
        except Exception:
            log.exception("re-check watch round failed; trying again in %s s", WATCH_SECONDS)
        await asyncio.sleep(WATCH_SECONDS)
