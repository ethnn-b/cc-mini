"""A keyless demo of the two safety boundaries the happy-path demo.py never shows:

  1. The path sandbox: a write outside the workspace is refused before it touches disk.
  2. The permission gate: a write inside the workspace is denied, the model sees "Denied"
     and reacts, then retries and the gate allows it.

Runs on the scripted FakeToolModel (no API key, no network); the ask callback stands in
for the y/N prompt, denying the first write and allowing the retry.

    uv run python scripts/demo_safety.py
"""

from __future__ import annotations

import shutil
from pathlib import Path

from langchain_core.messages import AIMessage

from ccmini import agent as agent_mod
from ccmini.model import FakeToolModel
from ccmini.permissions import Permissions
from ccmini.workspace import Workspace

SANDBOX = Path(__file__).resolve().parent.parent / "sandbox"

NOTE = "remember to water the plants\n"

# The model's scripted side: first reach outside the workspace (refused by the sandbox),
# then write inside it (denied by the gate), then retry the same write (allowed).
SCRIPT = [
    AIMessage(
        content="",
        tool_calls=[
            {"name": "write_file", "args": {"path": "../escape.txt", "content": "got out"},
             "id": "c1", "type": "tool_call"}
        ],
    ),
    AIMessage(
        content="",
        tool_calls=[
            {"name": "write_file", "args": {"path": "notes.txt", "content": NOTE},
             "id": "c2", "type": "tool_call"}
        ],
    ),
    AIMessage(
        content="",
        tool_calls=[
            {"name": "write_file", "args": {"path": "notes.txt", "content": NOTE},
             "id": "c3", "type": "tool_call"}
        ],
    ),
    AIMessage(
        content=(
            "I could not write outside the workspace, and my first write to notes.txt was "
            "denied. After you allowed the retry, I saved the note to notes.txt."
        )
    ),
]


class ScriptedGate:
    """Stands in for the human at the prompt: deny the first write, allow the rest.

    Prints each decision so the denial and the later approval are visible in the run.
    """

    def __init__(self) -> None:
        self.writes_seen = 0

    def __call__(self, action: str, detail: str) -> bool:
        self.writes_seen += action == "write"
        allow = not (action == "write" and self.writes_seen == 1)
        print(f"  gate asks: allow {action}? {detail}  ->  {'y' if allow else 'N'}")
        return allow


def main() -> None:
    if SANDBOX.exists():
        shutil.rmtree(SANDBOX)
    SANDBOX.mkdir(parents=True)

    model = FakeToolModel(responses=SCRIPT)
    workspace = Workspace(SANDBOX)
    permissions = Permissions(auto=False, ask=ScriptedGate())  # gate ON, so denials are real
    agent = agent_mod.build(model, workspace, permissions)

    print(f"workspace: {workspace.root}")
    print("(the gate is on; the model below will be told 'no' twice and has to react)\n")
    for chunk in agent.stream({"messages": [("user", "save a note to notes.txt")]}):
        for node, update in chunk.items():
            for message in update.get("messages", []):
                _show(node, message)

    print("\npermission log (what the gate decided):")
    for action, detail, allowed in permissions.log:
        print(f"  {'allow' if allowed else 'deny '} {action}: {detail}")

    escaped = (SANDBOX.parent / "escape.txt").exists()
    print(f"\nescape.txt written outside the workspace? {escaped}  (should be False)")
    note = SANDBOX / "notes.txt"
    print(f"notes.txt written inside the workspace?    {note.exists()}  (should be True)")


def _show(node: str, message) -> None:
    calls = getattr(message, "tool_calls", None)
    if calls:
        for call in calls:
            print(f"[{node}] tool: {call['name']}({call['args']})")
    elif getattr(message, "content", ""):
        label = "tool result" if message.type == "tool" else node
        print(f"[{label}] {message.content}")


if __name__ == "__main__":
    main()
