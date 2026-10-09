"""AnthropicLLM request shape, checked against a stubbed SDK client (no network)."""

from types import SimpleNamespace
from typing import Any

import anthropic
import httpx2
import pytest

from app.agent.llm import AnthropicLLM, LLMError
from app.agent.schemas import DraftSet


class StubMessages:
    def __init__(self, result: Any = None, error: Exception | None = None) -> None:
        self.kwargs: dict[str, Any] = {}
        self._result, self._error = result, error

    def parse(self, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        if self._error:
            raise self._error
        return self._result


def make_llm(messages: StubMessages) -> AnthropicLLM:
    llm = AnthropicLLM(api_key="test-key")
    llm._client = SimpleNamespace(beta=SimpleNamespace(messages=messages))  # type: ignore[assignment]
    return llm


def fake_response(parsed: Any = None, stop_reason: str = "end_turn") -> Any:
    usage = SimpleNamespace(
        input_tokens=10, output_tokens=5, cache_read_input_tokens=90, cache_creation_input_tokens=0
    )
    return SimpleNamespace(
        parsed_output=parsed, stop_reason=stop_reason, model="claude-sonnet-5-5", usage=usage
    )


def call(llm: AnthropicLLM, model: str = "claude-sonnet-5-5") -> Any:
    return llm.complete_json(
        model=model,
        system=["rules", "context"],
        prompt="the prompt",
        schema=DraftSet,
        effort="low",
        max_tokens=1000,
    )


def test_request_shape_with_caching_and_fallback() -> None:
    messages = StubMessages(fake_response(DraftSet(replies=[])))
    result = call(make_llm(messages))
    kw = messages.kwargs
    assert kw["model"] == "claude-sonnet-5-5"
    assert kw["output_format"] is DraftSet
    assert kw["output_config"] == {"effort": "low"}
    assert kw["messages"] == [{"role": "user", "content": "the prompt"}]
    # Only the last stable system block carries the cache breakpoint.
    assert kw["system"][0] == {"type": "text", "text": "rules"}
    assert kw["system"][1]["cache_control"] == {"type": "ephemeral"}
    assert kw["fallbacks"] == "default"
    assert kw["betas"] == ["server-side-fallback-2026-07-01"]

    assert result.usage.cache_read_tokens == 90
    assert result.parsed == DraftSet(replies=[])


def test_no_fallback_on_haiku() -> None:
    messages = StubMessages(fake_response(DraftSet(replies=[])))
    call(make_llm(messages), model="claude-haiku-5-5")
    assert "fallbacks" not in messages.kwargs
    assert "betas" not in messages.kwargs


def test_refusal_reported() -> None:
    result = call(make_llm(StubMessages(fake_response(None, stop_reason="refusal"))))
    assert result.refused and result.parsed is None


def test_api_errors_become_llm_error() -> None:
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    errors: list[Exception] = [
        anthropic.APIConnectionError(request=request),
        anthropic.RateLimitError(
            "slow down", response=httpx2.Response(429, request=request), body=None
        ),
    ]
    for error in errors:
        with pytest.raises(LLMError):
            call(make_llm(StubMessages(error=error)))


def test_unparseable_output_is_invalid_not_crash() -> None:
    result = call(make_llm(StubMessages(error=ValueError("bad json"))))
    assert result.parsed is None and result.stop_reason == "invalid_output"
