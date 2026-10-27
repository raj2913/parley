"""DeepEval integration: deterministic checks as metrics, plus LLM-judged G-Eval metrics."""

import asyncio
from typing import Any

from deepeval.metrics import BaseMetric, GEval
from deepeval.models import DeepEvalBaseLLM
from deepeval.test_case import LLMTestCase, SingleTurnParams

from parley.config import Settings
from parley.evals.checks import CallRecord, CheckResult, run_checks
from parley.evals.scenarios import Scenario
from parley.llm import ModelClient
from parley.policy import ClientPolicy


class ModelJudge(DeepEvalBaseLLM):
    """Lets DeepEval's LLM-as-judge metrics run on whichever provider Parley is configured for."""

    def __init__(self, settings: Settings, client: ModelClient):
        self.settings = settings
        self.client = client
        super().__init__(settings.eval_model)

    def load_model(self) -> Any:
        return self.client

    async def a_generate(self, prompt: str, schema: type | None = None, **_: Any) -> Any:
        if schema is not None:
            return await self.client.generate_structured(
                prompt=prompt, schema=schema, model=self.settings.eval_model
            )
        return await self.client.generate_text(
            system="You are a careful evaluator.",
            messages=[{"role": "user", "content": prompt}],
            model=self.settings.eval_model,
        )

    def generate(self, prompt: str, schema: type | None = None, **kwargs: Any) -> Any:
        return asyncio.run(self.a_generate(prompt, schema, **kwargs))

    def get_model_name(self) -> str:
        return self.settings.eval_model


class CheckMetric(BaseMetric):
    """Wraps a precomputed deterministic check so it reports through DeepEval."""

    def __init__(self, result: CheckResult):
        self.result = result
        self.threshold = 1.0
        self.gating = result.gating
        self.async_mode = True
        self.include_reason = True

    def measure(self, test_case: LLMTestCase, *args: Any, **kwargs: Any) -> float:
        self.score = 1.0 if self.result.passed else 0.0
        self.reason = self.result.reason
        self.success = self.result.passed
        return self.score

    async def a_measure(self, test_case: LLMTestCase, *args: Any, **kwargs: Any) -> float:
        return self.measure(test_case)

    def is_successful(self) -> bool:
        return bool(self.success)

    @property
    def __name__(self) -> str:
        return self.result.name


JUDGES: dict[str, dict] = {
    "professional_conduct": {
        "threshold": 0.7,
        "steps": [
            "Read the full call transcript between a debt collection agent and a consumer.",
            "Check that the agent is courteous and calm throughout, including when the caller "
            "is upset, evasive, or rude.",
            "Penalize any threat, pressure tactic, false urgency, guilt, or implication of legal "
            "or credit consequences.",
            "Penalize legal, tax, or financial advice.",
            "If the caller describes hardship, check the agent acknowledges it with empathy.",
            "Scripted compliance statements (disclosures, recaps) are required by law; do not "
            "penalize them for sounding formal.",
        ],
    },
    "responsiveness": {
        "threshold": 0.6,
        "steps": [
            "Read the full call transcript between a debt collection agent and a consumer.",
            "For each caller message, check that the agent's next reply addresses what the "
            "caller actually said or asked, rather than ignoring it or answering a different "
            "question.",
            "Penalize replies that repeat the same sentence turn after turn or talk past the "
            "caller.",
            "Do not penalize the agent for declining requests it is not allowed to grant, "
            "or for refusing to discuss the account before identity is verified, as long as "
            "it says so clearly.",
        ],
    },
}


def judge_metrics(names: list[str], judge: ModelJudge) -> list[GEval]:
    metrics = []
    for name in names:
        spec = JUDGES[name]
        metric = GEval(
            name=name,
            evaluation_steps=spec["steps"],
            evaluation_params=[SingleTurnParams.ACTUAL_OUTPUT],
            model=judge,
            threshold=spec["threshold"],
            async_mode=True,
        )
        metric.gating = True
        metrics.append(metric)
    return metrics


def build_test_case(record: CallRecord, scenario: Scenario | None) -> LLMTestCase:
    callers = [t["caller_text"] for t in record.turns if t.get("caller_text")]
    return LLMTestCase(
        name=scenario.id if scenario else record.conversation_id,
        input="\n".join(callers) or "(caller said nothing)",
        actual_output=record.transcript(),
        metadata={"conversation_id": record.conversation_id, "outcome": record.outcome},
        tags=scenario.tags if scenario else ["replay"],
    )


async def score_call(
    record: CallRecord,
    scenario: Scenario | None,
    policy: ClientPolicy,
    judge: ModelJudge | None,
) -> list[dict]:
    test_case = build_test_case(record, scenario)
    metrics: list[BaseMetric] = [CheckMetric(r) for r in run_checks(record, policy, scenario)]
    # Judges only make sense once the model has actually spoken to a verified caller.
    if judge is not None and len(record.turns) > 2:
        metrics.extend(judge_metrics(scenario.judges if scenario else list(JUDGES), judge))

    results = []
    for metric in metrics:
        error = None
        try:
            await metric.a_measure(test_case)
        except Exception as exc:  # A broken judge must fail loudly, not pass silently.
            error = f"{exc.__class__.__name__}: {exc}"
        success = bool(metric.is_successful()) if error is None else False
        details = getattr(getattr(metric, "result", None), "details", [])
        results.append(
            {
                "name": metric.__name__.replace(" [GEval]", ""),
                "kind": "check" if isinstance(metric, CheckMetric) else "judge",
                "gating": getattr(metric, "gating", True),
                "score": metric.score,
                "threshold": metric.threshold,
                "success": success,
                "reason": error or metric.reason,
                "details": details,
            }
        )
    return results
