"""End to end through the whole agent loop, offline.

FakeToolModel scripts the model's side: a tool call, then a final answer. create_react_agent
runs the tool for real against a temp workspace, so a green test here means the loop, tools,
and permission gate all fit together with no key and no network.
"""

from langchain_core.messages import AIMessage

from ccmini import agent as agent_mod
from ccmini.model import FakeToolModel
from ccmini.permissions import Permissions
from ccmini.workspace import Workspace


def _tool_call(name, args, call_id):
    return {"name": name, "args": args, "id": call_id, "type": "tool_call"}


def _build(tmp_path, responses, auto=True, ask=None):
    model = FakeToolModel(responses=responses)
    workspace = Workspace(tmp_path)
    perms = Permissions(auto=auto, ask=ask)
    return agent_mod.build(model, workspace, perms), perms


def test_agent_writes_a_file_then_answers(tmp_path):
    responses = [
        AIMessage(
            content="",
            tool_calls=[_tool_call("write_file", {"path": "hello.txt", "content": "hi"}, "c1")],
        ),
        AIMessage(content="Done. Created hello.txt."),
    ]
    agent, perms = _build(tmp_path, responses)

    answer = agent_mod.run(agent, "create hello.txt with 'hi'")

    assert answer == "Done. Created hello.txt."
    assert (tmp_path / "hello.txt").read_text() == "hi"
    assert perms.log == [("write", "create hello.txt (2 chars)", True)]


def test_agent_reads_then_edits_over_two_tool_turns(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    responses = [
        AIMessage(content="", tool_calls=[_tool_call("read_file", {"path": "a.py"}, "c1")]),
        AIMessage(
            content="",
            tool_calls=[_tool_call("edit_file", {"path": "a.py", "old": "x = 1", "new": "x = 2"}, "c2")],
        ),
        AIMessage(content="Changed x to 2."),
    ]
    agent, _ = _build(tmp_path, responses)

    answer = agent_mod.run(agent, "set x to 2 in a.py")

    assert answer == "Changed x to 2."
    assert (tmp_path / "a.py").read_text() == "x = 2\n"


def test_hitting_the_step_cap_returns_a_clear_note_not_a_crash(tmp_path):
    # A model that never stops calling tools. The loop must stop and say so, rather than
    # raising GraphRecursionError or handing back a raw tool result as if it were an answer.
    loop = AIMessage(content="", tool_calls=[_tool_call("list_files", {"path": "."}, "c")])
    agent, _ = _build(tmp_path, [loop] * 50)

    answer = agent_mod.run(agent, "loop forever", max_steps=2)

    assert "step limit" in answer.lower()
    assert "(empty)" not in answer  # not the raw tool result that used to leak through


def test_agent_delegates_to_a_bounded_subagent(tmp_path):
    # The top-level model calls run_subagent; the same FakeToolModel instance then plays
    # both sides of the nested create_react_agent call (its _index counter is shared), so
    # scripting all four replies in order drives the whole delegation end to end.
    responses = [
        AIMessage(
            content="",
            tool_calls=[_tool_call("run_subagent", {"task": "write hello.txt with 'hi'"}, "c1")],
        ),
        AIMessage(
            content="",
            tool_calls=[_tool_call("write_file", {"path": "hello.txt", "content": "hi"}, "c2")],
        ),
        AIMessage(content="Wrote hello.txt."),
        AIMessage(content="Delegated the write; hello.txt now exists."),
    ]
    agent, _ = _build(tmp_path, responses)

    answer = agent_mod.run(agent, "delegate writing hello.txt")

    assert answer == "Delegated the write; hello.txt now exists."
    assert (tmp_path / "hello.txt").read_text() == "hi"


def test_denied_mutation_surfaces_to_the_model(tmp_path):
    # The gate denies the write; the tool returns "Denied", which the model sees and
    # reports back instead of the file being created.
    responses = [
        AIMessage(
            content="",
            tool_calls=[_tool_call("write_file", {"path": "x.txt", "content": "no"}, "c1")],
        ),
        AIMessage(content="The write was denied, so I stopped."),
    ]
    agent, _ = _build(tmp_path, responses, auto=False, ask=lambda a, d: False)

    answer = agent_mod.run(agent, "create x.txt")

    assert "denied" in answer.lower()
    assert not (tmp_path / "x.txt").exists()
