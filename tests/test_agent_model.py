import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage

from mizani.agent.model import call_model, chat_model

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_endpoint_and_model_come_from_the_environment(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "http://ollama:11434/v1")
    monkeypatch.setenv("LLM_MODEL", "qwen3.5:9b-q4_K_M")
    llm = chat_model()
    assert llm.openai_api_base == "http://ollama:11434/v1"
    assert llm.model_name == "qwen3.5:9b-q4_K_M"


def test_every_request_has_thinking_off_and_temperature_zero(monkeypatch):
    monkeypatch.delenv("LLM_MODEL", raising=False)
    payload = chat_model()._get_request_payload([HumanMessage("hi")])
    assert payload["reasoning_effort"] == "none"
    assert payload["temperature"] == 0
    assert payload["model"] == "qwen3.5:4b-q4_K_M"


async def test_call_reports_model_tokens_and_latency():
    reply = AIMessage(
        "ok",
        usage_metadata={"input_tokens": 800, "output_tokens": 74, "total_tokens": 874},
        response_metadata={"model_name": "qwen3.5:4b-q4_K_M"},
    )
    call = await call_model(GenericFakeChatModel(messages=iter([reply])), [HumanMessage("hi")])
    assert (call.model, call.tokens, call.cost) == ("qwen3.5:4b-q4_K_M", 874, 0.0)
    assert call.latency_ms >= 0
    assert call.meta() == {"model": "qwen3.5:4b-q4_K_M", "tokens": 874, "cost": 0.0}
