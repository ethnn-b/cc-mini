"""The CLI's streaming display, offline.

_stream_turn prints each tool call and its result as the loop turns, then the final
answer, and returns the messages so a REPL turn can carry the conversation forward.
FakeToolModel scripts the model, so these assert on captured stdout with no key and no
network.
"""

from langchain_core.messages import AIMessage, HumanMessage

from ccmini import agent as agent_mod
from ccmini import cli
from ccmini import session as session_mod
from ccmini.config import Config
from ccmini.memory import Memory
from ccmini.model import FakeToolModel
from ccmini.permissions import Permissions
from ccmini.workspace import Workspace


def _tool_call(name, args, call_id):
    return {"name": name, "args": args, "id": call_id, "type": "tool_call"}


def _agent(tmp_path, responses, auto=True, ask=None):
    model = FakeToolModel(responses=responses)
    perms = Permissions(auto=auto, ask=ask)
    return agent_mod.build(model, Workspace(tmp_path), perms)


def test_stream_shows_tool_call_result_and_answer(tmp_path, capsys):
    responses = [
        AIMessage(
            content="",
            tool_calls=[_tool_call("write_file", {"path": "hi.txt", "content": "hello"}, "c1")],
        ),
        AIMessage(content="Done. Wrote hi.txt."),
    ]
    agent = _agent(tmp_path, responses)

    messages = cli._stream_turn(agent, [("user", "write hi.txt")], Config(workspace=str(tmp_path)))

    out = capsys.readouterr().out
    assert "-> write_file(" in out          # the tool call is shown as it happens
    assert "Wrote hi.txt (5 chars)" in out   # the tool result is echoed
    assert "Done. Wrote hi.txt." in out      # the final answer is printed
    assert (tmp_path / "hi.txt").read_text() == "hello"
    assert messages[-1].content == "Done. Wrote hi.txt."  # returned for the next REPL turn


def test_stream_surfaces_a_denied_write(tmp_path, capsys):
    responses = [
        AIMessage(
            content="",
            tool_calls=[_tool_call("write_file", {"path": "x.txt", "content": "no"}, "c1")],
        ),
        AIMessage(content="The write was denied, so I stopped."),
    ]
    agent = _agent(tmp_path, responses, auto=False, ask=lambda a, d: False)

    cli._stream_turn(agent, [("user", "write x.txt")], Config(workspace=str(tmp_path)))

    out = capsys.readouterr().out
    assert "Denied" in out
    assert not (tmp_path / "x.txt").exists()


def test_stream_reports_the_step_cap_instead_of_a_bare_tool_result(tmp_path, capsys):
    loop = AIMessage(content="", tool_calls=[_tool_call("list_files", {"path": "."}, "c")])
    agent = _agent(tmp_path, [loop] * 50)

    cli._stream_turn(agent, [("user", "loop")], Config(workspace=str(tmp_path), max_steps=2))

    out = capsys.readouterr().out
    assert "step limit" in out.lower()


def _feed_input(monkeypatch, lines):
    """Make bare input() calls in cli._repl replay `lines`, then raise EOFError, the same
    way a piped/closed stdin would end an interactive session."""
    it = iter(lines)

    def fake_input(prompt: str = "") -> str:
        try:
            return next(it)
        except StopIteration:
            raise EOFError from None

    monkeypatch.setattr("builtins.input", fake_input)


def test_repl_slash_help_memory_and_session(tmp_path, monkeypatch, capsys):
    agent = _agent(tmp_path, [AIMessage(content="unused")])
    workspace = Workspace(tmp_path)
    mem = Memory(task="do a thing", files=["a.py"], notes=["did it"])
    session_id = "20260704-000000-abcdef"
    _feed_input(monkeypatch, ["/help", "/memory", "/session", "exit"])

    cli._repl(agent, Config(workspace=str(tmp_path)), workspace, session_id, mem, [])

    out = capsys.readouterr().out
    assert "/reset" in out  # from /help
    assert "do a thing" in out and "a.py" in out  # from /memory
    assert str(session_mod.path(workspace, session_id)) in out  # from /session


def test_repl_reset_clears_history_and_memory(tmp_path, monkeypatch, capsys):
    responses = [
        AIMessage(
            content="",
            tool_calls=[_tool_call("write_file", {"path": "a.txt", "content": "x"}, "c1")],
        ),
        AIMessage(content="Wrote a.txt."),
    ]
    agent = _agent(tmp_path, responses)
    workspace = Workspace(tmp_path)
    mem = Memory()
    session_id = session_mod.new_id()
    _feed_input(monkeypatch, ["write a.txt", "/reset", "/memory", "exit"])

    cli._repl(agent, Config(workspace=str(tmp_path)), workspace, session_id, mem, [])

    out = capsys.readouterr().out
    assert "(no memory yet)" in out
    assert mem.task == "" and mem.files == [] and mem.notes == []
    saved_messages, saved_mem = session_mod.load(workspace, session_id)
    assert saved_messages == []
    assert saved_mem == Memory()


def test_repl_unknown_slash_command_does_not_reach_the_model(tmp_path, monkeypatch, capsys):
    agent = _agent(tmp_path, [AIMessage(content="should not be reached")])
    _feed_input(monkeypatch, ["/nope", "exit"])

    cli._repl(agent, Config(workspace=str(tmp_path)), Workspace(tmp_path), "sid", Memory(), [])

    out = capsys.readouterr().out
    assert "unknown command" in out
    assert "should not be reached" not in out


def test_after_turn_persists_a_readable_session(tmp_path):
    responses = [
        AIMessage(
            content="",
            tool_calls=[_tool_call("write_file", {"path": "hi.txt", "content": "hi"}, "c1")],
        ),
        AIMessage(content="Done."),
    ]
    agent = _agent(tmp_path, responses)
    workspace = Workspace(tmp_path)
    session_id = session_mod.new_id()
    mem = Memory()

    messages = cli._stream_turn(agent, [("user", "write hi.txt")], Config(workspace=str(tmp_path)))
    cli._after_turn(workspace, session_id, mem, "write hi.txt", 0, messages)

    loaded_messages, loaded_mem = session_mod.load(workspace, session_id)
    assert loaded_messages[-1].content == "Done."
    assert mem.files == ["hi.txt"]
    assert loaded_mem.files == ["hi.txt"]


def test_resolve_session_resumes_the_latest_saved_session(tmp_path):
    workspace = Workspace(tmp_path)
    session_id = session_mod.new_id()
    session_mod.save(workspace, session_id, [HumanMessage(content="earlier task")], Memory(task="earlier task"))
    cfg = Config(workspace=str(tmp_path), resume="latest")

    resolved = cli._resolve_session(workspace, cfg)

    assert resolved is not None
    loaded_id, messages, mem = resolved
    assert loaded_id == session_id
    assert mem.task == "earlier task"
    assert len(messages) == 1


def test_resolve_session_with_an_unknown_id_reports_and_returns_none(tmp_path, capsys):
    cfg = Config(workspace=str(tmp_path), resume="does-not-exist")

    resolved = cli._resolve_session(Workspace(tmp_path), cfg)

    assert resolved is None
    assert "does-not-exist" in capsys.readouterr().err
