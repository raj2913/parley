"""A scripted stand-in for the Claude API, so the pipeline can be tested without a network."""

import itertools
from collections import deque

from parley.intents import Intent, IntentResult
from parley.llm import Completion, LLMCall

_ids = itertools.count(1)


def intent(name: Intent | str, summary: str = "", flags=(), **fields) -> IntentResult:
    base = dict(
        amount=None,
        amount_frequency=None,
        date_of_birth=None,
        zip_code=None,
        preferred_date=None,
        callback_time=None,
        language=None,
    )
    base.update(fields)
    return IntentResult(
        intent=Intent(name), compliance_flags=list(flags), summary=summary or str(name), **base
    )


def text(value: str) -> list[dict]:
    return [{"type": "text", "text": value}]


def tool_use(name: str, **args) -> list[dict]:
    return [{"type": "tool_use", "id": f"toolu_{next(_ids)}", "name": name, "input": args}]


class FakeModel:
    """Pops one scripted classification per caller turn and one response per model call."""

    def __init__(self, intents: list[IntentResult], responses: list[list[dict]]):
        self.intents = deque(intents)
        self.responses = deque(responses)
        self.respond_requests: list[dict] = []

    async def classify(self, *, system: str, system_ref: str, user: str):
        call = LLMCall(
            "classify_intent", "fake", "low", system_ref, [{"role": "user", "content": user}], []
        )
        return self.intents.popleft(), call

    async def respond(
        self, *, system: str, system_ref: str, messages: list[dict], tools: list[dict]
    ):
        self.respond_requests.append({"messages": messages, "tools": [t["name"] for t in tools]})
        content = self.responses.popleft()
        stop = "tool_use" if any(b["type"] == "tool_use" for b in content) else "end_turn"
        call = LLMCall("respond", "fake", "low", system_ref, messages, [t["name"] for t in tools])
        call.response_content = content
        call.stop_reason = stop
        return Completion(content=content, stop_reason=stop), call
