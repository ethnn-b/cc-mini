"""The command line.

Two modes: a task argument runs once and prints the answer; no task opens an interactive
prompt that carries the conversation across turns. Flags override the environment-derived
Config. The only module that builds a live model and reads from the terminal; everything
below it is plain functions.

    cc-mini "add a docstring to utils.py"      # one-shot
    cc-mini --workspace ./myproj                # interactive REPL
    cc-mini --yes "run the tests and fix what fails"   # skip the permission gate
"""

from __future__ import annotations

import argparse
import sys

from langchain_core.messages import AIMessage, ToolMessage
from langgraph.errors import GraphRecursionError

from ccmini import agent as agent_mod
from ccmini import banner
from ccmini import memory as memory_mod
from ccmini import model as model_mod
from ccmini import sandbox
from ccmini import session as session_mod
from ccmini.config import Config
from ccmini.memory import Memory
from ccmini.permissions import Permissions
from ccmini.workspace import Workspace

SLASH_HELP = """\
/help     show this list
/memory   print the session's distilled working memory
/session  print the path to this session's saved transcript
/reset    clear this session's history and memory (stays in the REPL)
/exit     leave the REPL (same as /quit, or plain 'exit')
/quit     leave the REPL (same as /exit, or plain 'quit')"""

# How much of a tool result to echo. Full results still go to the model; this only keeps
# the terminal readable when a read or a command returns a lot.
DISPLAY_TOOL_RESULT_CHARS = 800


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="cc-mini",
        description="A tiny coding agent. Give it a task, or run it with no task for a REPL.",
    )
    parser.add_argument("task", nargs="*", help="the task to do; omit for an interactive session")
    parser.add_argument("--provider", help="ollama (default, keyless) | openai | anthropic")
    parser.add_argument("--model", help="model id; defaults to the provider's default")
    parser.add_argument("--workspace", help="directory the agent may read and write (default: .)")
    parser.add_argument("--base-url", help="override the provider endpoint (e.g. a local OpenAI server)")
    parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="skip the permission gate and allow every write, edit, and command",
    )
    parser.add_argument(
        "--allow-network",
        action="store_true",
        help="let run_bash reach the network (off by default; commands are otherwise offline)",
    )
    parser.add_argument("--max-steps", type=int, help="cap on agent loop iterations (default: 50)")
    parser.add_argument(
        "--max-subagent-steps", type=int, help="step cap for a run_subagent helper (default: 15)"
    )
    parser.add_argument(
        "--resume",
        metavar="latest|SESSION_ID",
        help="resume a saved session instead of starting a new one",
    )
    return parser.parse_args(argv)


def _config_from(args: argparse.Namespace) -> Config:
    cfg = Config.from_env()
    if args.provider:
        cfg.provider = args.provider
    if args.model:
        cfg.model = args.model
    if args.workspace:
        cfg.workspace = args.workspace
    if args.base_url:
        cfg.base_url = args.base_url
    if args.yes:
        cfg.auto_approve = True
    if args.allow_network:
        cfg.allow_network = True
    if args.max_steps:
        cfg.max_steps = args.max_steps
    if args.max_subagent_steps:
        cfg.max_subagent_steps = args.max_subagent_steps
    if args.resume:
        cfg.resume = args.resume
    return cfg


def _compact(args: dict) -> str:
    """One-line view of a tool's arguments, with long string values (file content, a big
    command) clipped so a call fits on a line. The model still gets the full arguments."""
    parts = []
    for key, value in args.items():
        if isinstance(value, str):
            shown = value.replace("\n", "\\n")
            if len(shown) > 60:
                shown = shown[:60] + f"... (+{len(value) - 60} chars)"
            parts.append(f"{key}={shown}")
        else:
            parts.append(f"{key}={value!r}")
    return ", ".join(parts)


def _render_new(messages: list, shown: int) -> int:
    """Print the messages produced since we last looked: the model's tool calls and text,
    and each tool result (clipped). Returns the new count so the next pass starts after it.
    Skips the 'sorry, need more steps' stub; the caller turns that into a clean note."""
    for msg in messages[shown:]:
        if isinstance(msg, AIMessage):
            for call in msg.tool_calls or []:
                print(f"  -> {call['name']}({_compact(call['args'])})")
            text = agent_mod._final_text(msg).strip()
            if text and not text.lower().startswith("sorry, need more steps"):
                print(f"\n{text}\n")
        elif isinstance(msg, ToolMessage):
            body = str(msg.content).strip()
            if len(body) > DISPLAY_TOOL_RESULT_CHARS:
                body = body[:DISPLAY_TOOL_RESULT_CHARS] + (
                    f"\n... (+{len(str(msg.content)) - DISPLAY_TOOL_RESULT_CHARS} chars, "
                    "full result went to the model)"
                )
            print("     " + body.replace("\n", "\n     "))
    return len(messages)


def _stream_turn(agent, messages: list, cfg: Config) -> list:
    """Run one turn, printing tool calls, results, and text as they happen (the cc feel),
    and return the full message list so a REPL turn can carry the conversation forward.

    The permission prompt fires inside a tool call mid-stream, so you see the call, then
    get asked, then see the result, the same order Claude Code shows. A cut-off loop ends
    with a clear note instead of a bare tool result."""
    shown = len(messages)  # skip history already on screen from earlier turns
    final = messages
    try:
        for state in agent.stream(
            {"messages": messages},
            stream_mode="values",
            config={"recursion_limit": 2 * cfg.max_steps + 1},
        ):
            final = state["messages"]
            shown = _render_new(final, shown)
    except GraphRecursionError:
        print("\n" + agent_mod._stopped_note(cfg.max_steps) + "\n")
        return final
    last = final[-1]
    answered = (
        isinstance(last, AIMessage)
        and not last.tool_calls
        and agent_mod._is_answer(agent_mod._final_text(last))
    )
    if not answered:  # cut off on a tool call or the out-of-steps stub; say so plainly
        print("\n" + agent_mod._final_answer(final, cfg.max_steps) + "\n")
    return final


def _live_error_hint(cfg: Config, exc: Exception) -> str:
    """Friendly message for the most common live-run failure: Ollama down or model not pulled."""
    msg = f"cc-mini: {type(exc).__name__}: {exc}"
    if cfg.provider == "ollama":
        msg += (
            "\nThe local model could not be reached. Check that Ollama is installed and running"
            " (`ollama serve`), and that the model is pulled:\n"
            f"  ollama pull {cfg.resolved_model()}"
        )
    return msg


def _resolve_session(workspace: Workspace, cfg: Config) -> tuple[str, list, Memory] | None:
    """Start a fresh session, or resume one. Returns (session_id, messages, memory), or
    None (having already printed an error) if --resume named a session that does not
    exist."""
    if not cfg.resume:
        return session_mod.new_id(), [], Memory()
    load_id = session_mod.latest(workspace) if cfg.resume == "latest" else cfg.resume
    if load_id is None:
        print(f"cc-mini: no saved sessions to resume in {workspace.root}", file=sys.stderr)
        return None
    try:
        messages, mem = session_mod.load(workspace, load_id)
    except FileNotFoundError:
        print(f"cc-mini: no saved session {load_id!r} in {workspace.root}", file=sys.stderr)
        return None
    return load_id, messages, mem


def _after_turn(
    workspace: Workspace, session_id: str, mem: Memory, task: str, before: int, messages: list
) -> None:
    """Fold a finished turn's new messages into memory and persist the session. `before`
    is how many messages existed prior to this turn, so only the turn's own messages (the
    user's line plus whatever the agent produced) are folded in."""
    memory_mod.update(mem, task, messages[before:])
    session_mod.save(workspace, session_id, messages, mem)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    cfg = _config_from(args)

    workspace = Workspace(cfg.workspace)
    permissions = Permissions(auto=cfg.auto_approve)
    resolved = _resolve_session(workspace, cfg)
    if resolved is None:
        return 1
    session_id, messages, mem = resolved

    try:
        model = model_mod.build(cfg)
    except (RuntimeError, ValueError) as exc:
        print(f"cc-mini: {exc}", file=sys.stderr)
        return 1
    agent = agent_mod.build(
        model,
        workspace,
        permissions,
        allow_network=cfg.allow_network,
        max_subagent_steps=cfg.max_subagent_steps,
        memory=mem,
    )

    print(banner.render(cfg, workspace, session_id))
    if cfg.auto_approve:
        print("permission gate OFF (--yes): writes, edits, and commands run without asking")
    if cfg.allow_network:
        print("run_bash network ON (--allow-network): shell commands can reach the network")
    elif not sandbox.available():
        print(
            "note: run_bash confinement is unavailable on this platform, so shell commands "
            "are not sandboxed to the workspace. The permission gate still applies."
        )
    if messages:
        print(f"resumed session {session_id} ({len(messages)} prior messages)")

    if args.task:
        task_text = " ".join(args.task)
        before = len(messages)
        messages.append(("user", task_text))
        print()
        try:
            messages = _stream_turn(agent, messages, cfg)
        except Exception as exc:  # noqa: BLE001 - surface a friendly hint, not a traceback
            print(_live_error_hint(cfg, exc), file=sys.stderr)
            return 1
        _after_turn(workspace, session_id, mem, task_text, before, messages)
        return 0

    return _repl(agent, cfg, workspace, session_id, mem, messages)


def _repl(
    agent, cfg: Config, workspace: Workspace, session_id: str, mem: Memory, messages: list
) -> int:
    """Interactive loop. Each turn carries the prior messages, so follow-ups have context.
    Lines starting with '/' are handled here directly, never sent to the model."""
    print("interactive session. type 'exit' or Ctrl-D to quit, /help for commands.\n")
    while True:
        try:
            line = input("cc-mini> ").strip()
        except EOFError:
            print()
            return 0
        if line in ("exit", "quit", "/exit", "/quit"):
            return 0
        if not line:
            continue
        if line == "/help":
            print(f"\n{SLASH_HELP}\n")
            continue
        if line == "/memory":
            print(f"\n{memory_mod.render(mem)}\n")
            continue
        if line == "/session":
            print(f"\n{session_mod.path(workspace, session_id)}\n")
            continue
        if line == "/reset":
            messages.clear()
            mem.task, mem.files, mem.notes = "", [], []
            session_mod.save(workspace, session_id, messages, mem)
            print("\nsession history and memory cleared.\n")
            continue
        if line.startswith("/"):
            print(f"\nunknown command {line!r}. /help for the list.\n")
            continue
        before = len(messages)
        messages.append(("user", line))
        print()
        try:
            messages = _stream_turn(agent, messages, cfg)
        except KeyboardInterrupt:
            messages.pop()
            print("\n[interrupted]\n")
            continue
        except Exception as exc:  # noqa: BLE001 - keep the session alive, just report
            messages.pop()  # drop the turn we could not answer
            print(_live_error_hint(cfg, exc) + "\n", file=sys.stderr)
            continue
        _after_turn(workspace, session_id, mem, line, before, messages)


if __name__ == "__main__":
    raise SystemExit(main())
