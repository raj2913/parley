"""Versioned instruction sets.

Instructions are files named `<name>.v<N>.md`. The highest version ships unless pinned with
PARLEY_PROMPT_<NAME>=v<N>. Every trace records the version and content hash of the exact
text the model received, so any reply can be traced back to the instructions behind it.
"""

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path

from parley.intents import COMPLIANCE_FLAG_DESCRIPTIONS, INTENT_DESCRIPTIONS
from parley.policy import ClientPolicy

PROMPTS_DIR = Path(__file__).parent
_FILE = re.compile(r"^(?P<name>[a-z_]+)\.v(?P<version>\d+)\.md$")


@dataclass(frozen=True)
class Prompt:
    name: str
    version: str
    text: str

    @property
    def sha(self) -> str:
        return hashlib.sha256(self.text.encode()).hexdigest()[:12]

    @property
    def ref(self) -> str:
        return f"{self.name}@{self.version}#{self.sha}"


def available_versions(name: str) -> list[int]:
    versions = []
    for path in PROMPTS_DIR.glob(f"{name}.v*.md"):
        if m := _FILE.match(path.name):
            versions.append(int(m["version"]))
    return sorted(versions)


def _template(name: str) -> tuple[str, str]:
    pinned = os.environ.get(f"PARLEY_PROMPT_{name.upper()}")
    version = pinned.lstrip("v") if pinned else str(max(available_versions(name)))
    return f"v{version}", (PROMPTS_DIR / f"{name}.v{version}.md").read_text()


def agent_prompt(policy: ClientPolicy) -> Prompt:
    version, template = _template("agent")
    text = template.format(agent_name=policy.agent_name, client_name=policy.client_name)
    return Prompt("agent", version, text)


def classifier_prompt() -> Prompt:
    version, template = _template("classifier")
    text = template.format(
        intent_list="\n".join(f"- `{k.value}`: {v}" for k, v in INTENT_DESCRIPTIONS.items()),
        flag_list="\n".join(f"- `{k}`: {v}" for k, v in COMPLIANCE_FLAG_DESCRIPTIONS.items()),
    )
    return Prompt("classifier", version, text)
