"""Distilled working memory: a small, deterministic summary of a session.

The transcript itself gets trimmed by `agent._pre_model_hook` when it grows long; memory is
a separate, much smaller record (the task, files touched, one note per turn) that survives
that trim, so a resumed or long-running session still knows what it was doing.

`update()` is heuristic, not a model call: it scans tool-call args and the final answer
text, which keeps behavior deterministic so `FakeToolModel` runs stay reproducible.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from langchain_core.messages import AIMessage, BaseMessage

MAX_FILES = 30
MAX_NOTES = 10
NOTE_CHARS = 100


@dataclass
class Memory:
    task: str = ""
    files: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _text(content) -> str:
    if isinstance(content, str):
        return content
    parts = [b.get("text", "") for b in content if isinstance(b, dict)]
    return "".join(parts)


def _touch(files: list[str], path: str) -> None:
    """Move `path` to the end (most-recently-touched), capped at MAX_FILES."""
    if path in files:
        files.remove(path)
    files.append(path)
    del files[:-MAX_FILES]


def _add_note(notes: list[str], text: str) -> None:
    note = text.strip().replace("\n", " ")
    if len(note) > NOTE_CHARS:
        note = note[:NOTE_CHARS] + "..."
    notes.append(note)
    del notes[:-MAX_NOTES]


def update(memory: Memory, task: str, new_messages: Sequence[BaseMessage]) -> None:
    """Fold the messages produced by one turn into `memory`, in place.

    `task` is the user's request for this turn (empty for a resumed turn with nothing
    new to set); `new_messages` is the slice of messages that turn produced, not the
    whole transcript.
    """
    if task:
        memory.task = task
    for msg in new_messages:
        if not isinstance(msg, AIMessage):
            continue
        for call in msg.tool_calls or []:
            args = call.get("args", {}) or {}
            touched = args.get("path") or args.get("url")
            if touched:
                _touch(memory.files, str(touched))
        if not msg.tool_calls:
            text = _text(msg.content).strip()
            if text and not text.lower().startswith("sorry, need more steps"):
                _add_note(memory.notes, text)


def render(memory: Memory) -> str:
    """Plain-text view for the `/memory` REPL command and for prompt injection."""
    lines: list[str] = []
    if memory.task:
        lines.append(f"task: {memory.task}")
    if memory.files:
        lines.append("files touched: " + ", ".join(memory.files))
    if memory.notes:
        lines.append("notes:")
        lines.extend(f"  - {n}" for n in memory.notes)
    return "\n".join(lines) if lines else "(no memory yet)"
