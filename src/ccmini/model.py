"""The chat model, behind one builder.

build() returns a LangChain chat model for the configured provider; each provider is a
lazy import, so the offline path needs none of the SDKs installed. Any model that supports
tool calling drops into the agent loop unchanged.

FakeToolModel is the offline stand-in: hand it a list of AIMessages (tool calls on the
early ones, plain text on the last) and it replays them in order, so a full pass through
the agent runs with no key and no network.
"""

from __future__ import annotations

import os
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import PrivateAttr

from ccmini.config import Config


def build(cfg: Config) -> BaseChatModel:
    """Construct the chat model for the configured provider.

    The provider SDKs are optional extras; import them only when selected.
    """
    provider = cfg.provider
    model = cfg.resolved_model()

    if provider == "ollama":
        # Local, keyless, the default. Tool calling works on tool-capable models
        # (qwen3-coder, qwen2.5-coder, qwen3, llama3.1, gpt-oss, ...). base_url or the
        # OLLAMA_HOST env var points at a non-default Ollama host.
        try:
            from langchain_ollama import ChatOllama
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("install langchain-ollama (it is a base dependency: run uv sync)") from exc
        kwargs: dict[str, Any] = {"model": model}
        if cfg.base_url:
            kwargs["base_url"] = cfg.base_url
        return ChatOllama(**kwargs)

    if provider == "openai":
        # Also the path for any OpenAI-compatible local server (vLLM, LM Studio,
        # llama.cpp --server): set base_url and the key is ignored by the server.
        try:
            from langchain_openai import ChatOpenAI
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("install the 'openai' extra: uv sync --extra openai") from exc
        kwargs = {"model": model}
        if cfg.base_url:
            kwargs["base_url"] = cfg.base_url
            kwargs["api_key"] = os.environ.get("OPENAI_API_KEY", "local")  # local servers ignore it
        return ChatOpenAI(**kwargs)

    if provider == "anthropic":
        try:
            from langchain_anthropic import ChatAnthropic
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("install the 'anthropic' extra: uv sync --extra anthropic") from exc
        return ChatAnthropic(model=model, max_tokens=cfg.max_tokens)

    raise ValueError(f"unknown provider {provider!r} (try ollama, openai, or anthropic)")


class FakeToolModel(BaseChatModel):
    """A scripted chat model for offline tests.

    Returns the given AIMessages in order, ignoring the incoming conversation. Put
    tool_calls on the early messages and plain text on the last one to drive a full
    pass through the agent loop. bind_tools is a no-op: the replies are already fixed,
    so the tool schema does not matter.
    """

    responses: list[AIMessage]
    _index: int = PrivateAttr(default=0)

    @property
    def _llm_type(self) -> str:
        return "fake-tool-model"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        if self._index >= len(self.responses):
            raise RuntimeError("FakeToolModel ran out of scripted responses")
        message = self.responses[self._index]
        self._index += 1
        return ChatResult(generations=[ChatGeneration(message=message)])

    def bind_tools(self, tools: Any, **kwargs: Any) -> "FakeToolModel":
        return self
