"""Model client: any OpenAI-compatible endpoint, chosen by `LLM_BASE_URL` and `LLM_MODEL`.

Thinking is off on every request and temperature is 0, so calls are fast and repeatable. Each
call reports the model, tokens and latency; they go into the audit log with the tool calls the
model asked for. `/v1` cannot set the context size per request, so set it on the server
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


def chat_model() -> ChatOpenAI:
    return ChatOpenAI(
        base_url=os.environ.get("LLM_BASE_URL", DEFAULT_BASE_URL),
        model=os.environ.get("LLM_MODEL", DEFAULT_MODEL),
        api_key="local",  # Ollama ignores it; the client refuses to start without one
        temperature=0,
        reasoning_effort="none",  # thinking off (checked on Ollama's /v1, NOTES §6b)
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

    def meta(self) -> dict:
        """`_meta` for the MCP tool calls this model call asked for, so the audit log has it."""
        return {"model": self.model, "tokens": self.tokens, "cost": self.cost}


async def call_model(llm: Runnable[LanguageModelInput, AIMessage], messages) -> ModelCall:
    started = time.perf_counter()
    message = await llm.ainvoke(messages)
    usage = message.usage_metadata or {}
    return ModelCall(
        message=message,
        model=message.response_metadata.get("model_name", "unknown"),
        tokens=usage.get("total_tokens", 0),
        latency_ms=round((time.perf_counter() - started) * 1000, 1),
    )
