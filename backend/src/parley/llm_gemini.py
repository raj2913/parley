"""Gemini implementation of `ModelClient` (works on the free tier of Google AI Studio).

History is stored in the canonical Anthropic-style block format; this module translates it to
Gemini `Content`s on the way out and translates responses back. Gemini attaches "thought
signatures" to function calls that must be returned with them, so they are kept on the
canonical blocks under `gemini_thought_signature`.
"""

import asyncio
import base64
import json
import time
import uuid
from collections import deque
from typing import Any, TypeVar

from google import genai
from google.genai import errors, types
from pydantic import BaseModel

from parley.config import Settings
from parley.intents import IntentResult
from parley.llm import Completion, LLMCall, ModelRefusal, ModelUnavailable

T = TypeVar("T", bound=BaseModel)

DIRECTIVE_PREFIX = "[Operator message from the call system. The caller did not say this.]\n"
REFUSAL_REASONS = {"SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "RECITATION"}
SIGNATURE_KEY = "gemini_thought_signature"


def _sig_out(raw: bytes | None) -> dict:
    return {SIGNATURE_KEY: base64.b64encode(raw).decode()} if raw else {}


def _sig_in(block: dict) -> bytes | None:
    value = block.get(SIGNATURE_KEY)
    return base64.b64decode(value) if value else None


def to_gemini_contents(messages: list[dict]) -> list[types.Content]:
    contents: list[types.Content] = []
    tool_names: dict[str, str] = {}

    def add(role: str, parts: list[types.Part]) -> None:
        if not parts:
            return
        if contents and contents[-1].role == role:
            contents[-1].parts.extend(parts)
        else:
            contents.append(types.Content(role=role, parts=parts))

    for message in messages:
        role, content = message["role"], message["content"]
        if role == "system":
            # Gemini has no mid-conversation system role; the directive rides on the user turn.
            add("user", [types.Part(text=DIRECTIVE_PREFIX + content)])
            continue
        blocks = [{"type": "text", "text": content}] if isinstance(content, str) else content
        parts = []
        for block in blocks:
            kind = block.get("type")
            if kind == "text" and block.get("text"):
                parts.append(types.Part(text=block["text"], thought_signature=_sig_in(block)))
            elif kind == "tool_use":
                tool_names[block["id"]] = block["name"]
                parts.append(
                    types.Part(
                        function_call=types.FunctionCall(
                            name=block["name"], args=block.get("input") or {}
                        ),
                        thought_signature=_sig_in(block),
                    )
                )
            elif kind == "tool_result":
                raw = block.get("content")
                try:
                    payload = json.loads(raw) if isinstance(raw, str) else {"result": raw}
                except json.JSONDecodeError:
                    payload = {"result": raw}
                parts.append(
                    types.Part(
                        function_response=types.FunctionResponse(
                            name=tool_names.get(block["tool_use_id"], "tool"),
                            response=payload if isinstance(payload, dict) else {"result": payload},
                        )
                    )
                )
            # Anthropic thinking blocks have no Gemini equivalent and are dropped.
        add("user" if role == "user" else "model", parts)
    return contents


def from_gemini_parts(parts: list[types.Part] | None) -> list[dict]:
    blocks: list[dict] = []
    for part in parts or []:
        if part.thought:
            continue
        if part.function_call:
            blocks.append(
                {
                    "type": "tool_use",
                    "id": part.function_call.id or f"gm_{uuid.uuid4().hex[:12]}",
                    "name": part.function_call.name,
                    "input": dict(part.function_call.args or {}),
                    **_sig_out(part.thought_signature),
                }
            )
        elif part.text:
            blocks.append({"type": "text", "text": part.text, **_sig_out(part.thought_signature)})
    return blocks


class _RateLimiter:
    def __init__(self, per_minute: int):
        self.per_minute = per_minute
        self.sent: deque[float] = deque()
        self.lock = asyncio.Lock()

    async def wait(self) -> None:
        async with self.lock:
            while True:
                now = time.monotonic()
                while self.sent and now - self.sent[0] > 60:
                    self.sent.popleft()
                if len(self.sent) < self.per_minute:
                    self.sent.append(now)
                    return
                await asyncio.sleep(60 - (now - self.sent[0]) + 0.1)


class GeminiModelClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.limiter = _RateLimiter(settings.gemini_requests_per_minute)
        self._client: genai.Client | None = None

    @property
    def client(self) -> genai.Client:
        if self._client is None:
            try:
                self._client = genai.Client()
            except ValueError as exc:
                raise ModelUnavailable("no Gemini credentials: set GEMINI_API_KEY") from exc
        return self._client

    async def _generate(self, model: str, contents: Any, config: types.GenerateContentConfig):
        # Free-tier per-minute limits reset quickly; wait them out rather than fail the call.
        delays = [10, 20, 40, 60, 60]
        for attempt in range(len(delays) + 1):
            await self.limiter.wait()
            try:
                return await self.client.aio.models.generate_content(
                    model=model, contents=contents, config=config
                )
            except errors.ClientError as exc:
                if exc.code in (401, 403):
                    raise ModelUnavailable("the Gemini API key was rejected") from exc
                if exc.code != 429:
                    raise
                if "per day" in str(exc).lower() or "perday" in str(exc).lower():
                    # Waiting won't help: the daily free quota resets once a day.
                    raise ModelUnavailable(
                        "Gemini's daily free-tier quota is used up for this key; it resets in "
                        "about a day, or use a key from another Google Cloud project"
                    ) from exc
                if attempt == len(delays):
                    raise ModelUnavailable("Gemini free-tier rate limit reached") from exc
            except errors.ServerError as exc:
                if attempt == len(delays):
                    raise ModelUnavailable(f"Gemini API error {exc.code}") from exc
            await asyncio.sleep(delays[attempt])
        raise ModelUnavailable("Gemini unavailable")

    @staticmethod
    def _finish(call: LLMCall, response: Any, started: float) -> list[types.Part] | None:
        call.latency_ms = int((time.perf_counter() - started) * 1000)
        candidate = response.candidates[0] if response.candidates else None
        parts = candidate.content.parts if candidate and candidate.content else None
        call.response_content = from_gemini_parts(parts)
        reason = candidate.finish_reason.name if candidate and candidate.finish_reason else None
        call.stop_reason = reason
        if response.usage_metadata:
            call.usage = response.usage_metadata.model_dump(mode="json", exclude_none=True)
        call.request_id = getattr(response, "response_id", None)
        call.served_by = getattr(response, "model_version", None) or call.model
        if candidate is None or reason in REFUSAL_REASONS:
            raise ModelRefusal(call)
        return parts

    async def classify(
        self, *, system: str, system_ref: str, user: str
    ) -> tuple[IntentResult, LLMCall]:
        model = self.settings.classifier_model
        messages = [{"role": "user", "content": user}]
        call = LLMCall("classify_intent", model, "default", system_ref, messages, [])
        started = time.perf_counter()
        response = await self._generate(
            model,
            user,
            types.GenerateContentConfig(
                system_instruction=system,
                response_mime_type="application/json",
                response_schema=IntentResult,
            ),
        )
        self._finish(call, response, started)
        parsed = response.parsed
        if not isinstance(parsed, IntentResult):
            try:
                parsed = IntentResult.model_validate_json(response.text or "")
            except ValueError as exc:
                raise ModelRefusal(call) from exc
        return parsed, call

    async def respond(
        self, *, system: str, system_ref: str, messages: list[dict], tools: list[dict]
    ) -> tuple[Completion, LLMCall]:
        model = self.settings.agent_model
        call = LLMCall(
            "respond", model, "default", system_ref, messages, [t["name"] for t in tools]
        )
        started = time.perf_counter()
        config = types.GenerateContentConfig(system_instruction=system)
        if tools:
            config.tools = [
                types.Tool(
                    function_declarations=[
                        types.FunctionDeclaration(
                            name=t["name"],
                            description=t["description"],
                            parameters_json_schema=t["input_schema"],
                        )
                        for t in tools
                    ]
                )
            ]
            config.automatic_function_calling = types.AutomaticFunctionCallingConfig(disable=True)
        response = await self._generate(model, to_gemini_contents(messages), config)
        self._finish(call, response, started)
        content = call.response_content
        stop = "tool_use" if any(b["type"] == "tool_use" for b in content) else "end_turn"
        if call.stop_reason == "MAX_TOKENS":
            stop = "max_tokens"
        return Completion(content=content, stop_reason=stop), call

    async def generate_text(self, *, system: str, messages: list[dict], model: str) -> str:
        response = await self._generate(
            model,
            to_gemini_contents(messages),
            types.GenerateContentConfig(system_instruction=system),
        )
        return (response.text or "").strip()

    async def generate_structured(self, *, prompt: str, schema: type[T], model: str) -> T:
        response = await self._generate(
            model,
            prompt,
            types.GenerateContentConfig(
                response_mime_type="application/json", response_schema=schema
            ),
        )
        if isinstance(response.parsed, schema):
            return response.parsed
        return schema.model_validate_json(response.text or "")
