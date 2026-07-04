# Concepts

What an agent actually is, taught from this codebase. If you have used Claude Code or Aider
and want to know what is going on underneath, this is the short version.

## An agent is a loop, not a prompt

A chatbot does one thing: text in, text out. An agent adds a loop and some tools. The model
does not just answer, it can ask to run a tool, see the result, and decide what to do next.
Concretely, one turn of the loop is:

1. Send the conversation so far to the model, along with the list of tools it may call.
2. The model replies. Either it answers in plain text (done), or it returns one or more
   tool calls: a tool name and arguments.
3. The harness runs each requested tool and appends the result to the conversation.
4. Go back to step 1.

That is the entire mechanism. The model never runs anything itself; it emits a request, and
your code decides whether and how to run it. In cc-mini the loop is LangGraph's
`create_react_agent` (see `agent.py`), and you can watch it turn by running `scripts/demo.py`,
which streams each tool call as it happens.

The name "react" is from the ReAct pattern (reason, then act): the model interleaves thinking
with tool calls instead of trying to answer in one shot.

## Tools are just functions with a description

A tool is a normal function plus a docstring the model can read. cc-mini has nine: six in
`tools.py`, two more in `web.py`, and one, `run_subagent`, built in `agent.py`:

- `read_file`, `list_files`, `search_files` are read-only. The model uses them to understand
  the project before it changes anything.
- `write_file`, `edit_file`, `run_bash` change the world.
- `web_fetch`, `web_search` are the controlled way back onto the network, since `run_bash` has
  none by default.
- `run_subagent` hands one fully-specified, self-contained subtask to a second, bounded
  `create_react_agent` built from the same tools (minus itself, so it cannot delegate again).
  See `docs/design-decisions.md` for why that is a separate loop rather than recursion.

The docstring matters more than you would expect. It is the only thing the model sees when it
decides which tool to call and how to fill in the arguments, so each one says what the tool
does and when to reach for it. The function signature becomes the argument schema
automatically (LangChain's `@tool` decorator reads the type hints).

When a tool returns, its return value goes back into the conversation as the observation for
that step. That is why our tools return error strings ("no such file", "Denied") instead of
raising: the model reads the string and adapts, the same way you would read an error in your
terminal and try again.

## Why the loop needs guardrails

The moment an agent can write files and run commands, it is acting on a real machine, and two
risks appear that a chatbot never has.

**It might touch the wrong files.** The model could ask to read `../../.ssh/id_rsa` or write
to `/etc`. cc-mini answers this with a workspace sandbox (`workspace.py`): every path the
model gives is resolved against one root directory and refused if it lands outside. Resolving
first and checking second is what defeats `..` and symlinks; a path that escapes never reaches
the disk, the tool returns an error string instead.

That path check only covers the file tools, though. `run_bash` hands a string to a real
shell, and `cat ../../etc/passwd` never passes through `Workspace.resolve`, so the same
boundary has to be enforced one level down, by the operating system. `sandbox.py` wraps the
command in the platform's own sandbox (macOS Seatbelt, Linux bubblewrap) so a shell command
can write only inside the workspace and, by default, cannot reach the network at all. This is
the lesson production agents like Codex CLI and Claude Code learned: a sandbox that lives in
your harness protects your tools, but the moment you spawn a shell you need the OS to hold the
line. The permission gate still asks first; the sandbox is what makes "no" mean "no" even
after you have said yes to the command.

**It might do something destructive before you can stop it.** Overwriting a file or running
`rm` is hard to take back. cc-mini answers this with a permission gate (`permissions.py`): the
five tools that change files or reach the network (`write_file`, `edit_file`, `run_bash`,
`web_fetch`, `web_search`) call `check(action, detail)` before they act, and on the CLI that is
a y/N prompt. If you say no, the tool returns "Denied", which the model sees and reacts to. In
a trusted context you pass `--yes` and the gate allows everything, which is the agent
equivalent of "I know what I am doing." `run_subagent` shares the same gate: the helper it
spawns is not separately gated because its own writes, edits, and commands still go through
this same check.

These two ideas, a sandbox and a confirmation gate, are not specific to cc-mini. They are the
core of how Claude Code and similar tools keep an autonomous loop from doing damage.

## Why a permission gate beats hoping

A common shortcut is to trust the model: write a careful system prompt asking it not to do
anything dangerous, and hope. That fails for the same reason "please be careful" fails for
people: it is advice, not a control. The model has no reason and no ability to enforce it, and
one confused step writes the file anyway.

The gate is a control. It sits between the model's request and the action, it cannot be
talked out of a denial, and because it is a plain object with a log, you can test exactly what
it would allow and inspect what it allowed after a run. The headline test in this repo
(`test_agent.py`) denies a write and checks that the file does not appear and that the model
sees the denial. That is the difference between a guardrail and a wish.

## Why provider-agnostic

The loop never names a model provider. `model.py` builds a LangChain chat model for whichever
provider you chose (a local open-weight model via Ollama by default, OpenAI or Anthropic
otherwise), and the agent takes it as an argument. Any model that supports tool calling drops in
unchanged, and tool calling is the one capability the loop actually requires. The
same seam is what makes the tests fast and free: `FakeToolModel` is a stand-in that returns a
scripted list of replies, so a full pass through the loop runs with no key and no network. You
script the model's side of the conversation and assert on what the tools did.
