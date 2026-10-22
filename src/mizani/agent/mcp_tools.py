"""The `mizani-triage` MCP tools as LangChain tools, for the agent and its model.

Built on the MCP Python SDK client (langchain-mcp-adapters only supports MCP SDK 1.x, and the
server is on 2.x). Each call can carry `_meta: {model, tokens, cost}` from the model call that
asked for it, so the server's audit log records them: `tool.ainvoke(call, with_meta(meta))`.
"""

import json
import os
from typing import Any

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import StructuredTool, ToolException
from mcp.client import Client
from mcp.types import Tool

# Our server on this machine (`uv run mizani-mcp`). Inside compose: http://mizani-mcp:8000/mcp
DEFAULT_URL = "http://127.0.0.1:8000/mcp"


def server_url() -> str:
    return os.environ.get("MIZANI_MCP_URL", DEFAULT_URL)


def with_meta(meta: dict | None) -> RunnableConfig:
    """Run config that makes a tool call send `meta` as the MCP `_meta`."""
    return {"configurable": {"mcp_meta": meta}}


async def load_tools(server: Any = None) -> list[StructuredTool]:
    """Every tool on the server. `server` is a URL (default `MIZANI_MCP_URL`) or, in tests, the
    in-process MCPServer."""
    server = server or server_url()
    async with Client(server) as client:
        tools = (await client.list_tools()).tools
    return [_as_langchain_tool(server, tool) for tool in tools]


async def read_json(uri: str, server: Any = None) -> Any:
    """A JSON resource from the server, e.g. `rules://sats/adult`."""
    async with Client(server or server_url()) as client:
        result = await client.read_resource(uri)
    return json.loads(result.contents[0].text)


def _as_langchain_tool(server: Any, tool: Tool) -> StructuredTool:
    async def call(config: RunnableConfig, **arguments: Any) -> tuple[str, Any]:
        meta = (config.get("configurable") or {}).get("mcp_meta")
        async with Client(server) as client:
            result = await client.call_tool(tool.name, arguments, meta=meta)
        text = "\n".join(c.text for c in result.content if c.type == "text")
        if result.is_error:
            # Comes back to the model as an error ToolMessage, so the agent can retry.
            raise ToolException(text)
        data = result.structured_content
        return text, data.get("result", data) if isinstance(data, dict) else data

    return StructuredTool(
        name=tool.name,
        description=tool.description or "",
        args_schema=tool.input_schema,
        coroutine=call,
        response_format="content_and_artifact",
        handle_tool_error=True,
        metadata={"read_only": bool(tool.annotations and tool.annotations.read_only_hint)},
    )
