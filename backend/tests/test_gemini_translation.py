import base64
import json

from google.genai import types

from parley.llm_gemini import DIRECTIVE_PREFIX, from_gemini_parts, to_gemini_contents


def test_history_translates_roles_directives_and_tool_round_trips():
    signature = base64.b64encode(b"sig").decode()
    history = [
        {"role": "user", "content": "[call connected]"},
        {"role": "assistant", "content": [{"type": "text", "text": "Hi, is this Maria?"}]},
        {"role": "user", "content": "Yes."},
        {"role": "system", "content": "TURN DIRECTIVE ..."},
        {
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": "", "signature": "x"},
                {
                    "type": "tool_use",
                    "id": "gm_1",
                    "name": "propose_payment_plan",
                    "input": {"monthly_amount": 155},
                    "gemini_thought_signature": signature,
                },
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "gm_1",
                    "content": json.dumps({"approved": True}),
                }
            ],
        },
    ]
    contents = to_gemini_contents(history)
    assert [c.role for c in contents] == ["user", "model", "user", "model", "user"]

    caller_turn = contents[2]
    assert caller_turn.parts[0].text == "Yes."
    assert caller_turn.parts[1].text.startswith(DIRECTIVE_PREFIX)

    call_part = contents[3].parts[0]
    assert call_part.function_call.name == "propose_payment_plan"
    assert call_part.thought_signature == b"sig"

    response = contents[4].parts[0].function_response
    assert response.name == "propose_payment_plan" and response.response == {"approved": True}


def test_response_parts_become_canonical_blocks():
    parts = [
        types.Part(text="thinking...", thought=True),
        types.Part(
            function_call=types.FunctionCall(
                name="propose_settlement", args={"lump_sum_amount": 900}
            ),
            thought_signature=b"abc",
        ),
        types.Part(text="Let me check that."),
    ]
    blocks = from_gemini_parts(parts)
    assert [b["type"] for b in blocks] == ["tool_use", "text"]
    assert blocks[0]["input"] == {"lump_sum_amount": 900}
    assert base64.b64decode(blocks[0]["gemini_thought_signature"]) == b"abc"
