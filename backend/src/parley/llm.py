"""Model clients that record every call for the trace viewer.

The agent depends on the `ModelClient` protocol, not on a vendor SDK. Conversation history is
kept in one canonical shape (Anthropic Messages content blocks) and each client translates it
for its provider, so the pipeline, guardrails, and evals are identical whichever model runs.
Unit tests drive the whole pipeline with a scripted fake.
"""

import time
from dataclasses import dataclass, field
from typing import Any, Protocol, TypeVar

import anthropic
from pydantic import BaseModel

from parley.config import Settings
from parley.intents import IntentResult

T = TypeVar("T", bound=BaseModel)

# Opt into server-side refusal fallbacks: if a safety classifier declines a request, the API
# retries it on Anthropic's recommended fallback model inside the same call.
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class ModelUnavailable(Exception):
    """The model could not be reached: missing credentials, outage, or rate limits."""


class ModelRefusal(Exception):
    def __init__(self, call: "LLMCall"):
        super().__init__(f"model declined the request ({call.stop_reason})")
        self.call = call


@dataclass
class LLMCall:
    purpose: str
    model: str
    effort: str
    system_ref: str
    request_messages: list[dict]
    tools: list[str]
    response_content: list[dict] = field(default_factory=list)
    stop_reason: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    latency_ms: int = 0
    request_id: str | None = None
    served_by: str | None = None

    def as_trace(self) -> dict:
        return {
            "purpose": self.purpose,
            "model": self.model,
            "served_by": self.served_by,
            "effort": self.effort,
            "system_ref": self.system_ref,
            "request_messages": self.request_messages,
            "tools": self.tools,
            "response_content": self.response_content,
            "stop_reason": self.stop_reason,
            "usage": self.usage,
            "latency_ms": self.latency_ms,
            "request_id": self.request_id,
        }


@dataclass
class Completion:
    content: list[dict]
    stop_reason: str | None


class ModelClient(Protocol):
    async def classify(
        self, *, system: str, system_ref: str, user: str
    ) -> tuple[IntentResult, LLMCall]: ...

    async def respond(
        self, *, system: str, system_ref: str, messages: list[dict], tools: list[dict]
    ) -> tuple[Completion, LLMCall]: ...

    # Used by the eval suite (simulated callers, judges), not by the agent.
    async def generate_text(self, *, system: str, messages: list[dict], model: str) -> str: ...

    async def generate_structured(self, *, prompt: str, schema: type[T], model: str) -> T: ...


def make_model_client(settings: Settings) -> ModelClient:
    if settings.provider == "gemini":
        from parley.llm_gemini import GeminiModelClient

        return GeminiModelClient(settings)
    return AnthropicModelClient(settings)


def _dump_content(blocks: list[Any]) -> list[dict]:
    return [block.model_dump(mode="json", exclude_none=True) for block in blocks]


class AnthropicModelClient:
    def __init__(self, settings: Settings, client: anthropic.AsyncAnthropic | None = None):
        self.settings = settings
        self.client = client or anthropic.AsyncAnthropic()

    @staticmethod
    def _system(text: str) -> list[dict]:
        # The instructions are identical on every turn, so cache them.
        return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]

    async def classify(
        self, *, system: str, system_ref: str, user: str
    ) -> tuple[IntentResult, LLMCall]:
        s = self.settings
        messages = [{"role": "user", "content": user}]
        call = LLMCall(
            purpose="classify_intent",
            model=s.classifier_model,
            effort=s.classifier_effort,
            system_ref=system_ref,
            request_messages=messages,
            tools=[],
        )
        started = time.perf_counter()
        response = await self._call(
            self.client.beta.messages.parse,
            model=s.classifier_model,
            max_tokens=16000,
            system=self._system(system),
            messages=messages,
            output_format=IntentResult,
            output_config={"effort": s.classifier_effort},
            betas=[FALLBACK_BETA],
            fallbacks="default",
        )
        self._finish(call, response, started)
        if response.stop_reason == "refusal" or response.parsed_output is None:
            raise ModelRefusal(call)
        return response.parsed_output, call

    async def respond(
        self, *, system: str, system_ref: str, messages: list[dict], tools: list[dict]
    ) -> tuple[Completion, LLMCall]:
        s = self.settings
        call = LLMCall(
            purpose="respond",
            model=s.agent_model,
            effort=s.agent_effort,
            system_ref=system_ref,
            request_messages=messages,
            tools=[t["name"] for t in tools],
        )
        started = time.perf_counter()
        kwargs: dict[str, Any] = {}
        if tools:
            kwargs["tools"] = tools
        response = await self._call(
            self.client.beta.messages.create,
            model=s.agent_model,
            max_tokens=16000,
            system=self._system(system),
            messages=messages,
            output_config={"effort": s.agent_effort},
            betas=[FALLBACK_BETA],
            fallbacks="default",
            **kwargs,
        )
        self._finish(call, response, started)
        if response.stop_reason == "refusal":
            raise ModelRefusal(call)
        return Completion(content=call.response_content, stop_reason=response.stop_reason), call

    async def generate_text(self, *, system: str, messages: list[dict], model: str) -> str:
        response = await self._call(
            self.client.beta.messages.create,
            model=model,
            max_tokens=16000,
            system=system,
            messages=messages,
            output_config={"effort": "low"},
            betas=[FALLBACK_BETA],
            fallbacks="default",
        )
        if response.stop_reason == "refusal":
            return ""
        return "".join(b.text for b in response.content if b.type == "text").strip()

    async def generate_structured(self, *, prompt: str, schema: type[T], model: str) -> T:
        response = await self._call(
            self.client.beta.messages.parse,
            model=model,
            max_tokens=16000,
            messages=[{"role": "user", "content": prompt}],
            output_format=schema,
            output_config={"effort": self.settings.eval_effort},
            betas=[FALLBACK_BETA],
            fallbacks="default",
        )
        if response.parsed_output is None:
            raise ValueError(f"model returned no {schema.__name__} ({response.stop_reason})")
        return response.parsed_output

    @staticmethod
    async def _call(method: Any, **kwargs: Any) -> Any:
        try:
            return await method(**kwargs)
        except anthropic.AuthenticationError as exc:
            raise ModelUnavailable("the Anthropic API key was rejected") from exc
        except (anthropic.RateLimitError, anthropic.APIConnectionError) as exc:
            raise ModelUnavailable(
                f"model temporarily unavailable ({exc.__class__.__name__})"
            ) from exc
        except anthropic.APIStatusError as exc:
            if exc.status_code >= 500:
                raise ModelUnavailable(f"model API error {exc.status_code}") from exc
            raise
        except TypeError as exc:
            # The SDK raises TypeError when it can't find any credentials at all.
            if "authentication" in str(exc):
                raise ModelUnavailable("no Anthropic credentials: set ANTHROPIC_API_KEY") from exc
            raise

    @staticmethod
    def _finish(call: LLMCall, response: Any, started: float) -> None:
        call.latency_ms = int((time.perf_counter() - started) * 1000)
        call.response_content = _dump_content(response.content)
        call.stop_reason = response.stop_reason
        call.usage = response.usage.model_dump(mode="json", exclude_none=True)
        call.request_id = getattr(response, "_request_id", None)
        call.served_by = response.model
