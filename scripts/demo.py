"""A keyless demo of the agent loop.

Runs cc-mini end to end against the scripted FakeToolModel: writes a small program to a
throwaway sandbox directory, runs it, and streams each tool call so you can watch the loop
turn. No API key, no network.

    uv run python scripts/demo.py

For a real run against a live model, use the CLI instead:

    uv sync --extra anthropic
    export ANTHROPIC_API_KEY=...
    uv run cc-mini --workspace ./sandbox "write fizzbuzz.py and run it"
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

FIZZBUZZ = '''\
for i in range(1, 16):
    if i % 15 == 0:
        print("FizzBuzz")
    elif i % 3 == 0:
        print("Fizz")
    elif i % 5 == 0:
        print("Buzz")
    else:
        print(i)
'''

# The model's scripted side of the conversation: write the file, run it, then summarise.
SCRIPT = [
    AIMessage(
        content="",
        tool_calls=[
            {"name": "write_file", "args": {"path": "fizzbuzz.py", "content": FIZZBUZZ},
             "id": "c1", "type": "tool_call"}
        ],
    ),
    AIMessage(
        content="",
        tool_calls=[
            {"name": "run_bash", "args": {"command": "python fizzbuzz.py"},
             "id": "c2", "type": "tool_call"}
        ],
    ),
    AIMessage(content="Wrote fizzbuzz.py and ran it; it prints 1 to 15 with Fizz/Buzz."),
]


def main() -> None:
    if SANDBOX.exists():
        shutil.rmtree(SANDBOX)
    SANDBOX.mkdir(parents=True)

    model = FakeToolModel(responses=SCRIPT)
    workspace = Workspace(SANDBOX)
    permissions = Permissions(auto=True)  # demo runs unattended, so no prompts
    agent = agent_mod.build(model, workspace, permissions)

    print(f"workspace: {workspace.root}\n")
    for chunk in agent.stream({"messages": [("user", "write fizzbuzz.py and run it")]}):
        for node, update in chunk.items():
            for message in update.get("messages", []):
                _show(node, message)

    print("\npermission log:")
    for action, detail, allowed in permissions.log:
        print(f"  {'allow' if allowed else 'deny '} {action}: {detail}")


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
