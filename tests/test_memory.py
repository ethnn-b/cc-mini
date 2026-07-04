"""Distilled working memory, offline. update() is a pure function over messages, so these
run with no model and no network.
"""

from langchain_core.messages import AIMessage

from ccmini import memory as memory_mod
from ccmini.memory import Memory


def _tool_call(name, args, call_id):
    return {"name": name, "args": args, "id": call_id, "type": "tool_call"}


def test_update_sets_task_and_tracks_a_touched_file():
    mem = Memory()
    turn = [AIMessage(content="", tool_calls=[_tool_call("write_file", {"path": "a.py"}, "c1")])]

    memory_mod.update(mem, "write a.py", turn)

    assert mem.task == "write a.py"
    assert mem.files == ["a.py"]


def test_update_moves_a_retouched_file_to_the_end():
    mem = Memory(files=["a.py", "b.py"])
    turn = [AIMessage(content="", tool_calls=[_tool_call("edit_file", {"path": "a.py"}, "c1")])]

    memory_mod.update(mem, "", turn)

    assert mem.files == ["b.py", "a.py"]


def test_update_caps_files_at_max():
    mem = Memory()
    for i in range(memory_mod.MAX_FILES + 5):
        turn = [AIMessage(content="", tool_calls=[_tool_call("read_file", {"path": f"f{i}.py"}, "c")])]
        memory_mod.update(mem, "", turn)

    assert len(mem.files) == memory_mod.MAX_FILES
    assert mem.files[-1] == f"f{memory_mod.MAX_FILES + 4}.py"


def test_update_appends_a_note_from_the_final_answer_and_caps_notes():
    mem = Memory()
    for i in range(memory_mod.MAX_NOTES + 3):
        memory_mod.update(mem, "", [AIMessage(content=f"did thing {i}")])

    assert len(mem.notes) == memory_mod.MAX_NOTES
    assert mem.notes[-1] == f"did thing {memory_mod.MAX_NOTES + 2}"


def test_update_ignores_the_out_of_steps_stub():
    mem = Memory()
    memory_mod.update(mem, "", [AIMessage(content="Sorry, need more steps to process this request.")])

    assert mem.notes == []


def test_render_lists_task_files_and_notes():
    mem = Memory(task="fix the bug", files=["a.py"], notes=["read a.py"])

    text = memory_mod.render(mem)

    assert "fix the bug" in text
    assert "a.py" in text
    assert "read a.py" in text


def test_render_empty_memory_says_so():
    assert memory_mod.render(Memory()) == "(no memory yet)"
