"""Session persistence: save a run's transcript and memory, resume it later.

Sessions live under `<workspace root>/.ccmini/sessions/<id>.json`, one file per session.
The id is time-sortable (`YYYYMMDD-HHMMSS-<hex>`), so `latest()` is just the newest filename.

Message (de)serialization is hand-rolled, not borrowed from a langchain internal helper:
the on-disk shape is four plain fields, so a saved session stays readable and stable
across langchain versions.
"""

from __future__ import annotations

import json
import secrets
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage

from ccmini.memory import Memory
from ccmini.workspace import Workspace

SESSIONS_DIRNAME = ".ccmini/sessions"


def new_id() -> str:
    """A fresh, time-sortable session id."""
    return f"{datetime.now():%Y%m%d-%H%M%S}-{secrets.token_hex(3)}"


def _sessions_dir(workspace: Workspace) -> Path:
    return workspace.root / SESSIONS_DIRNAME


def path(workspace: Workspace, session_id: str) -> Path:
    """Where a session's transcript is (or would be) saved. Public so the REPL's
    `/session` command can show it without reaching into a private helper."""
    return _sessions_dir(workspace) / f"{session_id}.json"


def _message_to_dict(message: BaseMessage) -> dict:
    data: dict = {"type": message.type, "content": message.content}
    if isinstance(message, AIMessage) and message.tool_calls:
        data["tool_calls"] = message.tool_calls
    if isinstance(message, ToolMessage):
        data["tool_call_id"] = message.tool_call_id
        if message.name:
            data["name"] = message.name
    return data


def _message_from_dict(data: dict) -> BaseMessage:
    kind = data["type"]
    content = data["content"]
    if kind == "human":
        return HumanMessage(content=content)
    if kind == "ai":
        return AIMessage(content=content, tool_calls=data.get("tool_calls") or [])
    if kind == "tool":
        return ToolMessage(
            content=content, tool_call_id=data["tool_call_id"], name=data.get("name", "")
        )
    if kind == "system":
        return SystemMessage(content=content)
    raise ValueError(f"unknown saved message type {kind!r}")


def save(workspace: Workspace, session_id: str, messages: list[BaseMessage], memory: Memory) -> Path:
    """Write the transcript and distilled memory for one session. Returns the file path."""
    session_path = path(workspace, session_id)
    session_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "id": session_id,
        "messages": [_message_to_dict(m) for m in messages],
        "memory": asdict(memory),
    }
    session_path.write_text(json.dumps(payload, indent=2))
    return session_path


def load(workspace: Workspace, session_id: str) -> tuple[list[BaseMessage], Memory]:
    """Read back a session's transcript and memory. Raises FileNotFoundError if it does
    not exist."""
    session_path = path(workspace, session_id)
    payload = json.loads(session_path.read_text())
    messages = [_message_from_dict(m) for m in payload["messages"]]
    memory = Memory(**payload.get("memory", {}))
    return messages, memory


def latest(workspace: Workspace) -> str | None:
    """The most recently created session id, or None if there are no saved sessions."""
    directory = _sessions_dir(workspace)
    if not directory.is_dir():
        return None
    ids = sorted(p.stem for p in directory.glob("*.json"))
    return ids[-1] if ids else None
