# CLAUDE.md

Working notes and build plan for this project. Read this first before writing any code.

## Summary

This is a miniature, open-source coding agent: the kind of thing Claude Code, Aider, or
the OpenAI "Codex" CLI are, stripped down to the part that matters. You give it a task in
plain English ("add a docstring to utils.py", "run the tests and fix what fails") and it
reads files, edits them, and runs commands to get it done, then tells you what it changed.
The agent is an LLM in a loop: the model proposes a tool call, the harness runs the tool,
the result goes back to the model, and that repeats until the model answers with no tool
call. That loop is the whole idea of an agent, and here it is small enough to read in one
sitting.

The point that makes this more than a chat wrapper is that the loop acts on a real machine,
so two things have to be true that a chatbot never worries about. First, the agent can only
touch one directory: every file path is resolved against a workspace root and rejected if it
escapes, so the model cannot wander into your home directory. Second, the tools that change
the world or reach the network (write, edit, run a command, fetch a URL, search the web) pass
through a permission gate that asks before acting, the same confirm-before-mutating model
Claude Code uses. The gate is a pure object you can point at and test, and the headline
behaviour is that you can watch a denied write come back to the model as a plain string it has
to react to, rather than the file silently changing.

## First-version scope

Get one honest end-to-end path working before adding anything clever.

- A task goes in. The agent runs the prebuilt LangGraph react loop over six tools.
- The six tools are read_file, list_files, search_files (read-only) and write_file,
  edit_file, run_bash (mutating, gated).
- Every path is confined to a workspace directory; an escape attempt returns an error
  string, it does not touch the disk.
- Mutating tools ask the permission gate first; denied calls return "Denied" and the agent
  adapts.
- The whole path runs offline against the scripted FakeToolModel, so `pytest` is green with
  no key and no network.

Everything after that (the live providers, the interactive REPL, streaming the transcript,
extra tools) is layered on once the skeleton does one task end to end.

## Folder layout

```
cc-mini/
  CLAUDE.md                 this file
  README.md                 readable overview
  docs/
    concepts.md             the agent loop, tools, and the permission idea, taught plainly
    design-decisions.md     the choices made, with pros/cons/alternatives
  src/ccmini/               the package
  tests/                    offline unit tests (FakeToolModel + a temp workspace)
  scripts/                  the keyless demo
  sandbox/                  throwaway dir the demo writes into, gitignored
  pyproject.toml            uv project + dependencies
  .python-version           3.13
```

## Planned modules (src/ccmini/)

- `config.py`: settings. Provider and model, the workspace directory, whether the permission
  gate is skipped, the loop's step cap and token cap. One dataclass, read from env where it
  makes sense and overridden by CLI flags.
- `workspace.py`: the path sandbox. `Workspace.resolve(path)` resolves a path against the
  root and raises if it escapes. This is the one safety boundary the file tools rely on, so
  it is tiny and tested directly.
- `permissions.py`: the gate. `Permissions.check(action, detail)` returns allow/deny. In auto
  mode it allows everything; otherwise an `ask` callback decides (a y/N prompt on the CLI, a
  stub in tests). It logs every decision. Pure object, no model or network.
- `model.py`: the chat model behind one builder. `build(cfg)` returns a LangChain chat model
  for the provider (ollama default and keyless, openai, anthropic), each a lazy import.
  `FakeToolModel` is the scripted offline stand-in, the role MockLLM plays in the sibling repos.
- `tools.py`: the core six tools (read_file, list_files, search_files, write_file, edit_file,
  run_bash), built as closures over a Workspace and a Permissions so they are plain functions
  a test can call. Docstrings are written for the model, since they are what it reads when
  choosing a tool.
- `sandbox.py`: the OS-level boundary under `run_bash` (macOS Seatbelt, Linux bubblewrap), so a
  shell command is confined to the workspace and off the network by default the same way the
  file tools already are, plus secret-scrubbing of the child's environment.
- `web.py`: the two tools that are the controlled way back onto the network now that `run_bash`
  has none by default: `web_fetch` (a URL's readable text) and `web_search` (Tavily if a key is
  set, else a keyless DuckDuckGo scrape). Standard library only, so the keyless path stays
  keyless.
- `agent.py`: the loop. `build(model, workspace, permissions)` hands the model and tools to
  LangGraph's `create_react_agent`. The system prompt lives here, along with `run_subagent`, a
  second, independent `create_react_agent` built from the same tools minus itself so bounded
  delegation cannot recurse. `run(agent, task)` invokes the graph and returns the final text.
- `memory.py`: a small, deterministic summary of a session (the task, files touched, one note
  per turn) extracted from tool calls and answers, not a model call, so it stays reproducible
  under `FakeToolModel` and survives even after old messages are trimmed from context.
- `session.py`: saves a run's transcript and memory under
  `<workspace>/.ccmini/sessions/<id>.json` and reloads one, so `--resume latest` or a session
  id picks a conversation back up.
- `banner.py`: the ASCII startup banner (workspace, model, provider, approval mode, session id,
  git branch) printed once so a run's state is visible at a glance.
- `cli.py`: the command line. One-shot mode (a task argument) and an interactive REPL, with
  slash commands (`/help`, `/memory`, `/session`, `/reset`, `/exit`) and a resume flag, that
  keeps the conversation across turns. The only place that builds a live model and reads the
  terminal.

## Planned tests/

- `test_permissions.py`: the gate in isolation. Auto allows and logs; the ask callback
  decides; a denial carries a reason. Pure, no model.
- `test_tools.py`: each tool against a temp workspace. The write/read roundtrip, the unique
  vs ambiguous edit, search and list, bash output capture, the gate denying mutations while
  reads still work, and the path sandbox blocking `..`.
- `test_agent.py`: end to end. FakeToolModel scripts a tool call then an answer;
  `create_react_agent` runs the tool for real against a temp dir. Covers a write-then-answer,
  a read-then-edit over two tool turns, and a denied mutation surfacing back to the model.

## Planned scripts/

- `demo.py`: a keyless demo. Points the agent at `sandbox/`, scripts the FakeToolModel to
  write a small program and run it, and streams each tool call so you can watch the loop. No
  key, no network. Prints how to do a live run with the CLI.

## Tech stack and defaults

- Python 3.13, managed with uv.
- Agent loop: LangGraph's `create_react_agent`. The interesting parts here are the tools and
  the gate, not the plumbing, so the loop is a prebuilt component rather than hand-rolled. The
  design notes name the hand-rolled alternative and why it was not chosen.
- Model: provider-agnostic via LangChain chat models. Default provider is a local, keyless,
  open-weight model served by Ollama (qwen3-coder:30b, which supports the tool calling the
  loop needs); hosted OpenAI and Anthropic are optional extras, and the openai path doubles as
  the route to any OpenAI-compatible local server. FakeToolModel sits underneath for offline
  runs. The only hard model requirement is tool calling.
- Tools: nine, deliberately, grown from the original six only as real tasks proved a need.
  Read, list, search, write, edit, run a command, fetch a URL, search the web, and delegate a
  bounded subtask. Enough to do real work, few enough to keep the surface honest.
- Safety: one workspace directory (paths that escape are refused) and a permission gate on the
  five tools that write, run a command, or reach the network, skippable with `--yes` in a
  trusted context.
- Output: a short plain summary of what changed, plus the permission log if you want it.

## Milestones

1. Skeleton: config and workspace in place, importable.
2. Permission gate, pure and unit-tested.
3. The six tools, sandboxed to the workspace, unit-tested against a temp dir.
4. FakeToolModel and the provider builder.
5. Agent assembled with create_react_agent and the system prompt.
6. End-to-end offline test (FakeToolModel scripts a write), green in pytest.
7. CLI: one-shot and interactive REPL.
8. Live provider wired (local Ollama default, keyless; OpenAI and Anthropic optional).
9. Keyless demo script that streams the loop.
10. README with the novel angle and run commands.

## Style rules

These apply to all docs and code comments in this repo.

- No em-dashes or en-dashes. Use commas, parentheses, or split the sentence. Normal hyphens
  fine.
- No AI-tells: avoid "In conclusion", "It is worth noting", "Moreover", "Furthermore",
  "delve", "leverage", "seamless", "robust", "comprehensive". No marketing tone. No emoji.
- Vary sentence length. Be concrete and technical. Write like a person who built the thing.

## Planned run commands

```bash
uv sync                                           # install (default keyless path needs no extras)
uv run pytest                                     # offline suite (FakeToolModel + temp dir)
uv run python scripts/demo.py                     # keyless demo of the loop (no Ollama needed)
ollama pull qwen3-coder:30b                       # the default local model (install Ollama first)
uv run cc-mini --workspace ./sandbox "write fizzbuzz.py and run it"
uv run cc-mini --workspace ./sandbox              # interactive REPL
uv sync --extra anthropic                         # optional: a hosted model instead of local
```

## Success criteria

- One task runs end to end: the agent reads, writes or edits, optionally runs a command, and
  summarises what it changed.
- Every mutation is gated, and a denial is visible as a string the model reacts to, not a
  silent change. Demonstrated in a test.
- No tool can touch a path outside the workspace. Demonstrated in a test.
- The whole offline path runs green with no API key and no network.

## Next steps

All ten milestones are done (see STATUS.md); the tool count grew from six to nine once real
tasks proved `web_fetch`, `web_search`, and `run_subagent` were missing, on the same "wait for
a real need" rule this section used to state for the first six. What is left is optional
polish, tracked in STATUS.md's own Next section (a live smoke test against a real provider,
`update_plan`/`edit_file` replace_all/a diff preview at the permission prompt from the Tier 2
review list, token-level streaming). Keep applying the same rule: grow the tool count, a
module, or the scope only once a real task proves something is missing, not speculatively.

## Status protocol (read by the orchestrator)

Keep STATUS.md current. The orchestrator session uses it to track this project, so treat it
as part of the work.

- Mirror the Milestones list above as checkboxes in STATUS.md. Tick one only when it is really
  done, meaning the code is written and its test passes, not when you start it.
- Update the Updated, Phase, and Progress lines whenever you make progress. Phase is one of:
  not started, in progress, blocked, done.
- Put anything you are stuck on under Blockers, with one line on what would unblock it. Write
  "None." when there is nothing.
- Commit STATUS.md when you tick a milestone, with a short plain message. The local git
  identity is already set, so a normal commit is fine.
