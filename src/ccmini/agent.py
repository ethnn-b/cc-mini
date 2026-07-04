"""The agent: a model, the tools, and the loop that ties them together.

build() hands the model and tools to LangGraph's create_react_agent: call the model, run
any tool calls it returns, feed the results back, repeat until it answers with no tool
call. run() invokes the graph on one task and returns the final text.

The one addition here is bounded delegation: run_subagent (_build_subagent_tool) is a
second, independent create_react_agent wrapped as a tool, built from the same base tools
minus itself, so delegation is exactly one level deep and cannot recurse.
"""

from __future__ import annotations

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, SystemMessage, trim_messages
from langchain_core.messages.utils import count_tokens_approximately
from langchain_core.tools import tool

# The prebuilt ReAct loop. In LangGraph 1.0 this moved to langchain.agents.create_agent;
# the langgraph.prebuilt name still works and keeps the dependency to langgraph alone,
# which is what a mini project wants. Swap the import when create_react_agent is removed.
from langgraph.errors import GraphRecursionError
from langgraph.prebuilt import create_react_agent

from ccmini import memory as memory_mod
from ccmini.memory import Memory
from ccmini.permissions import Permissions
from ccmini.tools import build as build_tools
from ccmini.workspace import Workspace

# The message list is append-only and re-sent every turn, so a long run would grow past
# the model's context window (a hard failure) or dilute it long before that (context rot).
# A pre_model_hook trims what each model call sees to the most recent messages that fit
# this budget, without mutating the stored state, so tool-call/result pairs stay intact.
MAX_HISTORY_TOKENS = 120_000

SYSTEM_PROMPT = """\
You are cc-mini, a small coding assistant that works inside a single project directory.

You have nine tools: read_file, list_files, search_files, write_file, edit_file, run_bash,
web_fetch, web_search, run_subagent.

How to work:
- Start by reading or listing what you need. Do not guess at file contents.
- Before editing a file, read it so your `old` text matches exactly.
- Make the smallest change that satisfies the request. Do not refactor unasked.
- Use run_bash to run tests or quick checks, and say what you ran and what it showed.
- Use web_search to find docs or solutions, then web_fetch the best link to read it. Reach
  for the web only when the answer is not already in the project.
- Use run_subagent to hand off one fully-specified, self-contained piece of work to a
  bounded helper (e.g. "find every TODO comment and list them"). Give it everything it
  needs, since it cannot ask you follow-up questions; do not delegate the whole task, and
  do not expect the helper to delegate further, it has no run_subagent of its own.
- write_file, edit_file, run_bash, web_fetch, and web_search ask the user for permission. If
  a call is denied, do not retry it; explain what you wanted to do and stop.
- When the task is done, give a short plain summary of what you changed.

read_file shows line numbers as a `lineno<tab>` prefix; that prefix is not part of the
file, so do not include it in `old` when you edit. Large files come back a page at a
time: read the next page with the offset the result gives you.

Work only inside the project directory. Keep your messages concise."""

SUBAGENT_SYSTEM_PROMPT = """\
You are a bounded helper agent, delegated one self-contained subtask by another agent. You
have eight tools: read_file, list_files, search_files, write_file, edit_file, run_bash,
web_fetch, web_search.

You cannot ask follow-up questions and have no delegation tool of your own, so finish the
subtask with what you were given. Work the way the main agent does: read before you edit,
make the smallest change that satisfies the subtask, and ask for permission before any
write, edit, command, or network call; stop and say so, do not retry, if one is denied.

When done, give a short plain final answer. It is returned as-is to whatever delegated to
you, not shown to a person, so state the outcome plainly rather than signing off."""


def _make_pre_model_hook(memory: Memory | None):
    """Build a pre_model_hook that trims history to the token budget and, if `memory` is
    given, prepends a rendering of it ahead of the trimmed messages for that model call
    only. `memory` is a live reference (cli.py mutates it turn to turn via memory.update),
    so each call sees the current state with no need to rebuild the agent. Returning
    llm_input_messages rather than mutating state means neither the trim nor the memory
    note ever lands in the stored transcript, so nothing accumulates across turns."""

    def _pre_model_hook(state: dict) -> dict:
        trimmed = trim_messages(
            state["messages"],
            strategy="last",
            token_counter=count_tokens_approximately,
            max_tokens=MAX_HISTORY_TOKENS,
            start_on="human",
            include_system=True,
            allow_partial=False,
        )
        if memory is not None:
            note = memory_mod.render(memory)
            summary = SystemMessage(
                content=f"Session memory (your own notes so far, not the user's request):\n{note}"
            )
            trimmed = [summary, *trimmed]
        return {"llm_input_messages": trimmed}

    return _pre_model_hook


def _build_subagent_tool(
    model: BaseChatModel,
    workspace: Workspace,
    permissions: Permissions,
    allow_network: bool,
    max_steps: int,
):
    """A run_subagent tool wrapping a second, independent create_react_agent built from
    the same base tools (without this tool), so delegation is exactly one level deep and
    can never recurse. The helper shares the workspace sandbox and the permission gate, so
    its own mutations are still gated the normal way; only its step budget is separate."""
    sub_tools = build_tools(workspace, permissions, allow_network=allow_network)
    sub_agent = create_react_agent(
        model, sub_tools, prompt=SUBAGENT_SYSTEM_PROMPT, pre_model_hook=_make_pre_model_hook(None)
    )

    @tool
    def run_subagent(task: str) -> str:
        """Delegate a fully-specified, self-contained subtask to a bounded helper agent
        that shares your tools, workspace, and permission gate but works within its own
        step limit and cannot ask you follow-up questions. Give it everything it needs in
        `task`; it has no memory of this conversation and no delegation tool of its own.
        Use it for a piece of work you can describe completely up front (e.g. "find every
        TODO comment and list them", "run the test suite and report which tests fail"),
        not for the whole task and not for something that needs back-and-forth. Returns
        the helper's final answer as plain text."""
        try:
            result = sub_agent.invoke(
                {"messages": [("user", task)]},
                config={"recursion_limit": 2 * max_steps + 1},
            )
        except GraphRecursionError:
            return _stopped_note(max_steps)
        return _final_answer(result["messages"], max_steps)

    return run_subagent


def build(
    model: BaseChatModel,
    workspace: Workspace,
    permissions: Permissions,
    allow_network: bool = False,
    max_subagent_steps: int = 15,
    memory: Memory | None = None,
):
    """Compile the agent: the prebuilt react loop over our tools plus a bounded delegation
    tool, with the system prompt. `memory`, if given, is folded into every model call by
    the pre_model_hook (see _make_pre_model_hook); pass None (the default) for the old
    behavior with no memory injected."""
    tools = build_tools(workspace, permissions, allow_network=allow_network)
    tools.append(
        _build_subagent_tool(model, workspace, permissions, allow_network, max_subagent_steps)
    )
    return create_react_agent(
        model, tools, prompt=SYSTEM_PROMPT, pre_model_hook=_make_pre_model_hook(memory)
    )


def _final_text(message: BaseMessage) -> str:
    """Pull plain text out of a message; some providers return a list of blocks."""
    content = message.content
    if isinstance(content, str):
        return content
    parts = [b.get("text", "") for b in content if isinstance(b, dict)]
    return "".join(parts)


def _is_answer(text: str) -> bool:
    """True if `text` reads like a real final answer, not an empty or out-of-steps stub.
    create_react_agent emits "Sorry, need more steps..." when it stops at the cap."""
    stripped = text.strip()
    return bool(stripped) and not stripped.lower().startswith("sorry, need more steps")


def _stopped_note(max_steps: int) -> str:
    return (
        f"Stopped before finishing: reached the step limit ({max_steps}). "
        "Re-run with a higher --max-steps or a narrower task."
    )


def _final_answer(messages: list[BaseMessage], max_steps: int) -> str:
    """The model's answer, or a clear 'stopped' note (plus the last real progress) when
    the loop ended on a tool call, a raw tool result, an empty message, or the
    out-of-steps stub. A real answer is an AIMessage with text and no pending tool call;
    a trailing ToolMessage means the loop was cut off mid-step, not that it finished."""
    last = messages[-1]
    if isinstance(last, AIMessage) and not last.tool_calls and _is_answer(_final_text(last)):
        return _final_text(last)
    for msg in reversed(messages):
        if isinstance(msg, AIMessage) and _is_answer(_final_text(msg)):
            return f"{_stopped_note(max_steps)}\n\nLast progress: {_final_text(msg)}"
    return _stopped_note(max_steps)


def run(agent, task: str, max_steps: int = 50) -> str:
    """Run the agent on one task and return its final answer.

    The recursion limit bounds the loop so a stuck run stops instead of spinning. One
    reason-then-act cycle is about two graph steps, so the limit is 2*max_steps+1. If the
    cap is hit, return a clear note instead of crashing or passing back a raw tool result.
    """
    try:
        result = agent.invoke(
            {"messages": [("user", task)]},
            config={"recursion_limit": 2 * max_steps + 1},
        )
    except GraphRecursionError:
        return _stopped_note(max_steps)
    return _final_answer(result["messages"], max_steps)
