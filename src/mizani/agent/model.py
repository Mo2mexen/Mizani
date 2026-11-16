"""Model client: any OpenAI-compatible endpoint, chosen by the admin in Admin › Model, or by
`LLM_BASE_URL` and `LLM_MODEL` (and `LLM_API_KEY` when the server checks keys) when no choice is
saved.

Thinking is off on every request and temperature is 0, so calls are fast and repeatable. Each
call reports the model, tokens (in, out and total) and latency; they go into the audit log with
the tool calls the model asked for. `/v1` cannot set the context size per request, so set it on
the server
(Ollama: 8192 or more), or a prompt with every tool definition can be cut off without an error.
"""

import os
import time
from dataclasses import dataclass

from langchain_core.language_models import LanguageModelInput
from langchain_core.messages import AIMessage
from langchain_core.runnables import Runnable
from langchain_openai import ChatOpenAI

# Ollama on this machine. Inside compose, `.env.example` points at the ollama service instead.
DEFAULT_BASE_URL = "http://127.0.0.1:11434/v1"
DEFAULT_MODEL = "qwen3.5:4b-q4_K_M"


def chat_model(choice: dict | None = None) -> ChatOpenAI:
    """The model the agent calls: the admin's choice in Admin › Model when one is saved
    (`settings.model`), otherwise `.env`. A saved choice never borrows `.env`'s key, so a key
    meant for one server is not sent to another."""
    env = os.environ
    if choice:
        base_url, model, key = choice["base_url"], choice["model"], choice.get("api_key")
        effort = choice.get("reasoning_effort")
    else:
        base_url = env.get("LLM_BASE_URL", DEFAULT_BASE_URL)
        model, key = env.get("LLM_MODEL", DEFAULT_MODEL), env.get("LLM_API_KEY")
        effort = env.get("LLM_REASONING_EFFORT", "none")
    return ChatOpenAI(
        base_url=base_url,
        model=model,
        # Ollama ignores the key, but the client refuses to start without one. A model server
        # that checks keys (vLLM, llama.cpp with --api-key) gets it from LLM_API_KEY.
        api_key=key or "local",
        temperature=0,
        # Thinking off (checked on Ollama's /v1). Servers that reject the field: set it empty.
        reasoning_effort=effort or None,
        timeout=300,  # a cold model load on a slow disk or a CPU-only machine took ~2 min
        max_retries=1,
    )


@dataclass(frozen=True)
class ModelCall:
    message: AIMessage
    model: str
    tokens: int
    latency_ms: float
    cost: float = 0.0  # local open-weights model
    tokens_in: int = 0  # the prompt: system text, chart signs, tool definitions, the entry
    tokens_out: int = 0  # the model's reply: its tool calls

    def meta(self) -> dict:
        """`_meta` for the MCP tool calls this model call asked for, so the audit log has it."""
        return {
            "model": self.model,
            "tokens": self.tokens,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "cost": self.cost,
        }


async def call_model(llm: Runnable[LanguageModelInput, AIMessage], messages) -> ModelCall:
    started = time.perf_counter()
    message = await llm.ainvoke(messages)
    usage = message.usage_metadata or {}
    return ModelCall(
        message=message,
        model=message.response_metadata.get("model_name", "unknown"),
        tokens=usage.get("total_tokens", 0),
        latency_ms=round((time.perf_counter() - started) * 1000, 1),
        tokens_in=usage.get("input_tokens", 0),
        tokens_out=usage.get("output_tokens", 0),
    )
