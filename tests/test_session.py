"""Session persistence, offline: save/load roundtrips a transcript and memory, and
`latest()` finds the newest of several saved sessions. No model, no network.
"""

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from ccmini import session
from ccmini.memory import Memory
from ccmini.workspace import Workspace


def _tool_call(name, args, call_id):
    return {"name": name, "args": args, "id": call_id, "type": "tool_call"}


def test_save_then_load_roundtrips_messages_and_memory(tmp_path):
    workspace = Workspace(tmp_path)
    sid = session.new_id()
    messages = [
        HumanMessage(content="write hi.txt"),
        AIMessage(content="", tool_calls=[_tool_call("write_file", {"path": "hi.txt"}, "c1")]),
        ToolMessage(content="Wrote hi.txt", tool_call_id="c1", name="write_file"),
        AIMessage(content="Done."),
    ]
    mem = Memory(task="write hi.txt", files=["hi.txt"], notes=["Done."])

    session.save(workspace, sid, messages, mem)
    loaded_messages, loaded_mem = session.load(workspace, sid)

    assert [m.type for m in loaded_messages] == ["human", "ai", "tool", "ai"]
    assert loaded_messages[1].tool_calls[0]["name"] == "write_file"
    assert loaded_messages[2].tool_call_id == "c1"
    assert loaded_mem == mem


def test_latest_finds_the_newest_of_several_sessions(tmp_path):
    workspace = Workspace(tmp_path)
    older, newer = "20260101-000000-aaaaaa", "20260102-000000-bbbbbb"
    session.save(workspace, older, [], Memory())
    session.save(workspace, newer, [], Memory())

    assert session.latest(workspace) == newer


def test_latest_with_no_sessions_returns_none(tmp_path):
    assert session.latest(Workspace(tmp_path)) is None


def test_loading_a_missing_session_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        session.load(Workspace(tmp_path), "does-not-exist")
