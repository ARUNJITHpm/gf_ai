"""The only place that talks to an LLM API (Anthropic or OpenAI, see LLM_PROVIDER).

Everything else uses the `LLMClient` protocol, so tests run on `FakeLLM` and never touch
the network.
"""

import os
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

import anthropic
import openai
from anthropic.types.beta import BetaOutputConfigParam
from pydantic import BaseModel

from app.config import Settings

# Message text must not leave the machine except to the chosen LLM API (spec §2, §12).
# LangGraph pulls in LangSmith; force its tracing off regardless of the environment.
for _var in ("LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2", "LANGCHAIN_TRACING"):
    os.environ[_var] = "false"

# Models that support server-side refusal fallback with `fallbacks: "default"`.
_FALLBACK_MODELS = {"claude-sonnet-5-5", "claude-opus-5-5", "claude-opus-5", "claude-fable-5-1"}
_FALLBACK_BETA = "server-side-fallback-2026-07-01"

Effort = Literal["low", "medium", "high", "xhigh", "max"]


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
            self.cache_read_tokens + other.cache_read_tokens,
            self.cache_write_tokens + other.cache_write_tokens,
        )


@dataclass
class LLMResult[T: BaseModel]:
    parsed: T | None  # None when the output didn't validate or the model declined
    stop_reason: str | None
    model: str
    usage: Usage = field(default_factory=Usage)

    @property
    def refused(self) -> bool:
        return self.stop_reason == "refusal"


class LLMError(Exception):
    """The API could not be reached or rejected the request (auth, rate limit, outage)."""


class LLMClient(Protocol):
    def complete_json[T: BaseModel](
        self,
        *,
        model: str,
        system: Sequence[str],
        prompt: str,
        schema: type[T],
        effort: Effort,
        max_tokens: int,
    ) -> LLMResult[T]:
        """`system` blocks are stable text (cached); `prompt` is the per-request part."""
        ...


class AnthropicLLM:
    def __init__(self, api_key: str | None, timeout: float = 30.0, max_retries: int = 1) -> None:
        # With no key the SDK still tries other credential sources (e.g. `ant auth login`).
        kwargs: dict[str, Any] = {"timeout": timeout, "max_retries": max_retries}
        if api_key:
            kwargs["api_key"] = api_key
        self._client = anthropic.Anthropic(**kwargs)

    def complete_json[T: BaseModel](
        self,
        *,
        model: str,
        system: Sequence[str],
        prompt: str,
        schema: type[T],
        effort: Effort,
        max_tokens: int,
    ) -> LLMResult[T]:
        system_blocks: list[dict[str, Any]] = [{"type": "text", "text": text} for text in system]
        if system_blocks:
            # Cache everything up to the end of the stable system text.
            system_blocks[-1]["cache_control"] = {"type": "ephemeral"}

        extra: dict[str, Any] = {}
        if model in _FALLBACK_MODELS:
            extra = {"betas": [_FALLBACK_BETA], "fallbacks": "default"}

        try:
            response = self._client.beta.messages.parse(
                model=model,
                max_tokens=max_tokens,
                system=system_blocks,  # type: ignore[arg-type]
                messages=[{"role": "user", "content": prompt}],
                output_format=schema,
                output_config=BetaOutputConfigParam(effort=effort),
                **extra,
            )
        except anthropic.APIConnectionError as exc:
            raise LLMError("could not reach the Anthropic API") from exc
        except anthropic.APIStatusError as exc:
            raise LLMError(f"Anthropic API error {exc.status_code}") from exc
        except anthropic.AnthropicError as exc:
            raise LLMError(type(exc).__name__) from exc
        except ValueError:
            # Output that doesn't validate against `schema` surfaces as a pydantic error.
            return LLMResult(parsed=None, stop_reason="invalid_output", model=model)

        usage = response.usage
        return LLMResult(
            parsed=response.parsed_output,
            stop_reason=response.stop_reason,
            model=response.model,
            usage=Usage(
                input_tokens=usage.input_tokens or 0,
                output_tokens=usage.output_tokens or 0,
                cache_read_tokens=usage.cache_read_input_tokens or 0,
                cache_write_tokens=usage.cache_creation_input_tokens or 0,
            ),
        )


class OpenAILLM:
    """OpenAI Chat Completions with structured outputs (Pydantic `response_format`).

    `effort` is ignored: non-reasoning models such as gpt-4o-mini have no such setting.
    OpenAI caches long identical prompt prefixes automatically, so the stable system text
    goes first as one system message.
    """

    def __init__(self, api_key: str | None, timeout: float = 30.0, max_retries: int = 1) -> None:
        kwargs: dict[str, Any] = {"timeout": timeout, "max_retries": max_retries}
        if api_key:
            kwargs["api_key"] = api_key
        self._client = openai.OpenAI(**kwargs)

    def complete_json[T: BaseModel](
        self,
        *,
        model: str,
        system: Sequence[str],
        prompt: str,
        schema: type[T],
        effort: Effort,
        max_tokens: int,
    ) -> LLMResult[T]:
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": "\n\n".join(system)})
        messages.append({"role": "user", "content": prompt})
        try:
            completion = self._client.chat.completions.parse(
                model=model,
                messages=messages,  # type: ignore[arg-type]
                response_format=schema,
                max_completion_tokens=max_tokens,
            )
        except (openai.LengthFinishReasonError, openai.ContentFilterFinishReasonError):
            return LLMResult(parsed=None, stop_reason="invalid_output", model=model)
        except openai.APIConnectionError as exc:
            raise LLMError("could not reach the OpenAI API") from exc
        except openai.APIStatusError as exc:
            raise LLMError(f"OpenAI API error {exc.status_code}") from exc
        except openai.OpenAIError as exc:
            raise LLMError(type(exc).__name__) from exc
        except ValueError:
            return LLMResult(parsed=None, stop_reason="invalid_output", model=model)

        choice = completion.choices[0]
        usage = Usage()
        if completion.usage is not None:
            details = completion.usage.prompt_tokens_details
            cached = (details.cached_tokens or 0) if details else 0
            usage = Usage(
                input_tokens=completion.usage.prompt_tokens - cached,
                output_tokens=completion.usage.completion_tokens,
                cache_read_tokens=cached,
            )
        if choice.message.refusal:
            return LLMResult(
                parsed=None, stop_reason="refusal", model=completion.model, usage=usage
            )
        return LLMResult(
            parsed=choice.message.parsed,
            stop_reason=choice.finish_reason,
            model=completion.model,
            usage=usage,
        )


def make_llm(settings: Settings) -> LLMClient:
    """The real client for the configured provider."""
    if settings.llm_provider == "openai":
        key = settings.openai_api_key
        return OpenAILLM(api_key=key.get_secret_value() if key else None)
    anthropic_key = settings.anthropic_api_key
    return AnthropicLLM(api_key=anthropic_key.get_secret_value() if anthropic_key else None)


@dataclass
class FakeCall:
    model: str
    system: list[str]
    prompt: str
    schema: type[BaseModel]
    effort: Effort


class FakeLLM:
    """Test double. Queue responses per schema; every call is recorded for assertions.

    A queued item may be a model instance (returned as parsed output), `None` (invalid
    output), the string "refusal", or an exception to raise.
    """

    def __init__(self) -> None:
        self.calls: list[FakeCall] = []
        self._queues: dict[type[BaseModel], list[Any]] = {}

    def queue(self, schema: type[BaseModel], *items: Any) -> None:
        self._queues.setdefault(schema, []).extend(items)

    def complete_json[T: BaseModel](
        self,
        *,
        model: str,
        system: Sequence[str],
        prompt: str,
        schema: type[T],
        effort: Effort,
        max_tokens: int,
    ) -> LLMResult[T]:
        self.calls.append(FakeCall(model, list(system), prompt, schema, effort))
        queue = self._queues.get(schema) or []
        if not queue:
            raise AssertionError(f"FakeLLM: nothing queued for {schema.__name__}")
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        usage = Usage(input_tokens=100, output_tokens=50)
        if item == "refusal":
            return LLMResult(parsed=None, stop_reason="refusal", model=model, usage=usage)
        return LLMResult(parsed=item, stop_reason="end_turn", model=model, usage=usage)

    def prompts_for(self, schema: type[BaseModel]) -> list[str]:
        """Full text (system + prompt) of every call made with `schema`."""
        return ["\n".join([*c.system, c.prompt]) for c in self.calls if c.schema is schema]
