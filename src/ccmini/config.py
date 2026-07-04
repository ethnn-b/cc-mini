"""Settings, in one place.

Everything that changes how a run behaves lives here: which provider and model,
which directory the agent may touch, whether the permission gate is skipped, and the
loop's safety caps. Read from the environment where it makes sense, overridden by CLI
flags in cli.py.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# A sensible default model per provider, used when CCMINI_MODEL is unset. The ollama
# default is Qwen3-Coder 30B: an open-weight, agentic-coding model that supports tool
# calling and runs locally, so the default path needs no API key. See docs/concepts.md
# and the README for lighter and heavier alternatives.
DEFAULT_MODELS = {
    "ollama": "qwen3-coder:30b",
    "anthropic": "claude-opus-4-8",
    "openai": "gpt-4o",
}


@dataclass
class Config:
    """One run's configuration."""

    provider: str = "ollama"  # ollama (default, keyless) | anthropic | openai
    model: str = ""  # empty means "use the default for this provider"
    workspace: str = "."  # the only directory the tools may read or write
    auto_approve: bool = False  # True skips the permission gate (every mutation runs)
    allow_network: bool = False  # True lets run_bash reach the network (off by default)
    max_steps: int = 50  # reason-then-act cycles before a stuck run is stopped
    max_subagent_steps: int = 15  # step cap for a run_subagent helper agent
    max_tokens: int = 8000  # output cap for providers that take one (e.g. Anthropic)
    base_url: str = ""  # override the provider endpoint (e.g. a local OpenAI-compatible server)
    resume: str = ""  # "" starts a new session; "latest" or a session id resumes one

    def resolved_model(self) -> str:
        """The model to use, falling back to the provider default."""
        if self.model:
            return self.model
        return DEFAULT_MODELS.get(self.provider, "")

    @classmethod
    def from_env(cls) -> Config:
        """Build a Config from environment variables, leaving CLI flags to override."""
        return cls(
            provider=os.environ.get("CCMINI_PROVIDER", cls.provider),
            model=os.environ.get("CCMINI_MODEL", ""),
            workspace=os.environ.get("CCMINI_WORKSPACE", cls.workspace),
            auto_approve=os.environ.get("CCMINI_AUTO_APPROVE", "").lower() in ("1", "true", "yes"),
            allow_network=os.environ.get("CCMINI_ALLOW_NETWORK", "").lower() in ("1", "true", "yes"),
            base_url=os.environ.get("CCMINI_BASE_URL", ""),
        )
