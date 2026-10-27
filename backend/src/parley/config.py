from functools import lru_cache
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PARLEY_", env_file=BACKEND_ROOT / ".env", extra="ignore"
    )

    database_url: str = "postgresql+asyncpg://parley@localhost:5433/parley"

    # Which model provider runs the agent, the classifier, and the eval suite.
    provider: Literal["anthropic", "gemini"] = "anthropic"

    # The agent speaks to callers; the classifier labels each caller turn. Both default to the
    # same model so a release is evaluated against exactly what ships. Left unset, these
    # follow the provider's default model.
    agent_model: str = ""
    agent_effort: str = "low"
    classifier_model: str = ""
    classifier_effort: str = "low"
    # Used by the eval suite only: simulated callers and LLM judges.
    eval_model: str = ""
    eval_effort: str = "medium"

    # Free-tier Gemini keys are rate limited; calls are paced to stay under this. The default
    # Gemini model is flash-lite because full flash queues for 10-40s per call on the free tier.
    gemini_requests_per_minute: int = 15

    policy_path: Path = BACKEND_ROOT / "policies" / "acme_recovery.yaml"
    scenarios_dir: Path = BACKEND_ROOT / "evals" / "scenarios"

    # Hard ceilings so a confused conversation can't run forever.
    max_turns: int = 30
    max_guard_retries: int = 2
    max_tool_iterations: int = 4

    cors_origins: list[str] = ["http://localhost:5173"]

    # Lets the console place a demo call "at" a given local time when it's outside calling
    # hours for real. Turn off anywhere real consumers could be called.
    allow_simulated_local_time: bool = True

    @model_validator(mode="after")
    def _default_models(self) -> "Settings":
        default = DEFAULT_MODELS[self.provider]
        for name in ("agent_model", "classifier_model", "eval_model"):
            if not getattr(self, name):
                setattr(self, name, default)
        return self


DEFAULT_MODELS = {"anthropic": "claude-opus-5-5", "gemini": "gemini-3.5-flash-lite"}


@lru_cache
def get_settings() -> Settings:
    # Also exports provider API keys from backend/.env for the SDKs; real env vars win.
    load_dotenv(BACKEND_ROOT / ".env", override=False)
    return Settings()
