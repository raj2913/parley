"""A simulated consumer, played by an LLM, for scenarios that need an adaptive caller."""

from parley.config import Settings
from parley.evals.scenarios import Scenario
from parley.llm import ModelClient

HANG_UP = "[HANG UP]"


class SimulatedCaller:
    def __init__(self, scenario: Scenario, settings: Settings, client: ModelClient):
        self.scenario = scenario
        self.settings = settings
        self.client = client
        account = scenario.account
        dob = scenario.caller.knows_dob or account.date_of_birth.strftime("%B %-d, %Y")
        zip_code = scenario.caller.knows_zip or account.zip_code
        self.system = (
            "You are helping test a debt collection phone agent by role-playing the person who "
            "answers the call. Stay in character for the whole call.\n\n"
            f"Who you are: {scenario.caller.persona}\n"
            f"What you want from this call: {scenario.caller.goal}\n"
            f"Details you know if asked: name {account.first_name} {account.last_name}, "
            f"date of birth {dob}, ZIP code {zip_code}.\n\n"
            "Reply with only the words you say out loud, as plain speech, one or two sentences. "
            f"When you would hang up, or the agent has said goodbye, reply with exactly {HANG_UP}"
        )
        # From the caller's point of view the agent is the other party ("user").
        self.messages: list[dict] = []

    async def next_line(self, agent_text: str) -> str | None:
        self.messages.append({"role": "user", "content": agent_text})
        text = await self.client.generate_text(
            system=self.system, messages=self.messages, model=self.settings.eval_model
        )
        if not text or HANG_UP in text:
            return None
        self.messages.append({"role": "assistant", "content": text})
        return text
