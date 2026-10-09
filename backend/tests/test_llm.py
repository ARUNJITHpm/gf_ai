"""AnthropicLLM request shape, checked against a stubbed SDK client (no network)."""

from types import SimpleNamespace
from typing import Any

import anthropic
import httpx2
import openai
import pytest

from app.agent.llm import AnthropicLLM, LLMError, OpenAILLM, make_llm
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


def make_anthropic(messages: StubMessages) -> AnthropicLLM:
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
    result = call(make_anthropic(messages))
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
    call(make_anthropic(messages), model="claude-haiku-5-5")
    assert "fallbacks" not in messages.kwargs
    assert "betas" not in messages.kwargs


def test_refusal_reported() -> None:
    result = call(make_anthropic(StubMessages(fake_response(None, stop_reason="refusal"))))
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
            call(make_anthropic(StubMessages(error=error)))


def test_unparseable_output_is_invalid_not_crash() -> None:
    result = call(make_anthropic(StubMessages(error=ValueError("bad json"))))
    assert result.parsed is None and result.stop_reason == "invalid_output"


# --- OpenAI -----------------------------------------------------------------------------


class StubCompletions:
    def __init__(self, result: Any = None, error: Exception | None = None) -> None:
        self.kwargs: dict[str, Any] = {}
        self._result, self._error = result, error

    def parse(self, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        if self._error:
            raise self._error
        return self._result


def make_openai(completions: StubCompletions) -> OpenAILLM:
    llm = OpenAILLM(api_key="test-key")
    llm._client = SimpleNamespace(chat=SimpleNamespace(completions=completions))  # type: ignore[assignment]
    return llm


def openai_completion(parsed: Any = None, refusal: str | None = None) -> Any:
    message = SimpleNamespace(parsed=parsed, refusal=refusal, content=None)
    usage = SimpleNamespace(
        prompt_tokens=1200,
        completion_tokens=80,
        prompt_tokens_details=SimpleNamespace(cached_tokens=1024),
    )
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason="stop")],
        usage=usage,
        model="gpt-4o-mini-2024-07-18",
    )


def test_openai_request_shape_and_usage() -> None:
    completions = StubCompletions(openai_completion(DraftSet(replies=[])))
    result = make_openai(completions).complete_json(
        model="gpt-4o-mini",
        system=["rules", "context"],
        prompt="the prompt",
        schema=DraftSet,
        effort="low",
        max_tokens=1000,
    )
    kw = completions.kwargs
    assert kw["model"] == "gpt-4o-mini"
    assert kw["response_format"] is DraftSet
    assert kw["max_completion_tokens"] == 1000
    # Stable text first, as one system message, so OpenAI's prefix cache can reuse it.
    assert kw["messages"] == [
        {"role": "system", "content": "rules\n\ncontext"},
        {"role": "user", "content": "the prompt"},
    ]
    assert "effort" not in kw and "reasoning_effort" not in kw
    assert result.parsed == DraftSet(replies=[])
    assert (result.usage.input_tokens, result.usage.cache_read_tokens) == (176, 1024)
    assert result.usage.output_tokens == 80


def test_openai_refusal_and_errors() -> None:
    refused = make_openai(StubCompletions(openai_completion(refusal="I can't help with that")))
    result = refused.complete_json(
        model="gpt-4o-mini", system=[], prompt="p", schema=DraftSet, effort="low", max_tokens=10
    )
    assert result.refused and result.parsed is None

    request = httpx2.Request("POST", "https://api.openai.com/v1/chat/completions")
    error = openai.APIConnectionError(request=request)
    with pytest.raises(LLMError, match="OpenAI"):
        make_openai(StubCompletions(error=error)).complete_json(
            model="gpt-4o-mini", system=[], prompt="p", schema=DraftSet, effort="low", max_tokens=10
        )


def test_make_llm_picks_provider() -> None:
    from app.config import Settings

    base = {"api_key": "x" * 16, "anthropic_api_key": "a", "openai_api_key": "o"}
    assert isinstance(make_llm(Settings(_env_file=None, **base)), AnthropicLLM)  # type: ignore[arg-type, call-arg]
    openai_settings = Settings(_env_file=None, llm_provider="openai", **base)  # type: ignore[arg-type, call-arg]
    assert isinstance(make_llm(openai_settings), OpenAILLM)
