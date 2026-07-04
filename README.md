# cc-mini

A tiny open-source coding agent. Give it a task in plain English and it reads your files,
edits them, and runs commands to get it done, then tells you what it changed. It is a
miniature Claude Code: an LLM in a loop with nine tools and a permission gate, small enough
to read in one sitting.

> Status: working end to end. The loop, the nine tools, the permission gate, the path
> sandbox, the OS sandbox on run_bash, session persistence and resume, distilled working
> memory, the CLI (one-shot and REPL, with a startup banner and slash commands), and a
> keyless demo are all in place, with the whole offline path green in pytest. It runs on
> local open-weight models by default (no API key), with hosted OpenAI and Anthropic as
> optional extras. See `CLAUDE.md` for the build plan and `STATUS.md` for the milestone
> checklist.

## The idea

An agent is not a clever prompt, it is a loop. The model proposes a tool call, the harness
runs the tool, the result goes back to the model, and that repeats until the model answers
with no tool call. cc-mini is that loop and nothing more, so you can see exactly where the
model acts and where your code stays in control.

What makes it more than a chat wrapper is that the loop touches a real machine, so two
things have to be true that a chatbot never worries about:

- **It stays in one directory.** Every file path is resolved against a workspace root and
  refused if it escapes (no `..` out of the project, no absolute paths elsewhere). `run_bash`
  spawns a real shell, so it is held to the same boundary by the OS sandbox (macOS Seatbelt,
  Linux bubblewrap): a command can write only inside the workspace and has no network access
  unless you pass `--allow-network`. The model can only read and write where you pointed it.
- **It asks before reaching outside this conversation.** The tools that change files or
  touch the network (write, edit, run a command, fetch a URL, search the web) pass through a
  permission gate that prompts first, the same confirm-before-acting model Claude Code uses. A
  denied call comes back to the model as a plain "Denied" string it has to react to, so nothing
  happens behind your back. Pass `--yes` to skip the gate in a context you trust.

## The nine tools

| Tool           | Gated? | What it does                                              |
| -------------- | ------ | --------------------------------------------------------- |
| `read_file`    | no     | Read a text file, line-numbered and paged with offset/limit |
| `list_files`   | no     | List a directory                                          |
| `search_files` | no     | Grep file contents (ripgrep; skips vendored/generated dirs) |
| `write_file`   | yes    | Create or overwrite a file                                |
| `edit_file`    | yes    | Replace one exact occurrence of a string in a file        |
| `run_bash`     | yes    | Run a shell command, sandboxed to the workspace, no network |
| `web_fetch`    | yes    | Fetch a URL and return its readable text                  |
| `web_search`   | yes    | Search the web for titles, URLs, and snippets             |
| `run_subagent` | no*    | Delegate one bounded, self-contained subtask to a helper agent |

The first six do real work on the project; two more are the controlled way back onto the
network, since `run_bash` has none by default. `web_search` uses [Tavily](https://tavily.com)
when `TAVILY_API_KEY` is set and falls back to a best-effort, keyless DuckDuckGo scrape
otherwise. `run_subagent` is not separately gated (*), because the helper it spawns shares
the same workspace sandbox and permission gate, so its own writes, edits, and commands are
still asked for individually; the helper has no `run_subagent` of its own, so delegation is
exactly one level deep. Enough to do real work, few enough that the surface stays honest.

## Setup

Uses [uv](https://docs.astral.sh/uv/) and Python 3.13.

```bash
uv sync
```

The default runs on a local open-weight model through [Ollama](https://ollama.com), so there
is no API key anywhere. Install Ollama, then pull the default model (or a lighter one from the
table below):

```bash
# install Ollama from https://ollama.com, then:
ollama pull qwen3-coder:30b      # the default: agentic coding + tool calling, ~18GB
```

The offline path (the test suite and the demo) needs neither Ollama nor a key.

### Which model

The one hard requirement is **tool calling**: the loop is useless without it, so pick a model
that supports it. Good open-weight choices, by what your machine can run:

| Model (`ollama pull ...`)    | Size  | Notes                                                        |
| ---------------------------- | ----- | ------------------------------------------------------------ |
| `qwen3-coder:30b` (default)  | ~18GB | Best local agentic coder here; needs ~32GB RAM / 24GB VRAM   |
| `qwen2.5-coder:14b`          | ~9GB  | Strong coder, lighter; good on a 16-24GB machine             |
| `qwen2.5-coder:7b`           | ~4.7GB| Runs almost anywhere; weaker at long tool loops              |
| `llama3.1:8b`                | ~4.9GB| Very stable tool calling; a safe small default               |
| `gpt-oss:120b`               | ~65GB | OpenAI open weights; top tier if you have the hardware       |

Switch with `--model`, e.g. `cc-mini --model qwen2.5-coder:7b "..."`. Newer families (Qwen 3.5/3.6,
GLM, Gemma 4) appear on the [Ollama tools list](https://ollama.com/search?c=tools) as they ship;
anything tagged **tools** there will work.

### Hosted models (optional)

The loop is provider-agnostic, so a hosted model is a flag and an extra install:

```bash
uv sync --extra anthropic     # then export ANTHROPIC_API_KEY=...  and --provider anthropic
uv sync --extra openai        # then export OPENAI_API_KEY=...      and --provider openai
```

The `openai` extra also points at any OpenAI-compatible **local** server (vLLM, LM Studio,
llama.cpp), which stays keyless:

```bash
uv run cc-mini --provider openai --base-url http://localhost:8000/v1 --model my-local-model "..."
```

## Usage

```bash
# keyless demo: scripts the model with FakeToolModel and streams the loop, no Ollama needed
uv run python scripts/demo.py

# one task against the local model (ollama + qwen3-coder by default)
uv run cc-mini --workspace ./sandbox "write fizzbuzz.py and run it"

# interactive session: follow-ups keep their context
uv run cc-mini --workspace ./sandbox

# skip the permission gate in a context you trust
uv run cc-mini --yes --workspace ./sandbox "run the tests and fix what fails"

# pick up where a previous run or REPL left off
uv run cc-mini --workspace ./sandbox --resume latest

# run the offline suite (no Ollama, no key, no network)
uv run pytest
```

Flags: `--provider` (ollama | openai | anthropic), `--model`, `--workspace`, `--base-url`,
`--yes`, `--allow-network`, `--max-steps`, `--max-subagent-steps`, `--resume` (`latest` or a
session id). The same settings read from `CCMINI_PROVIDER`, `CCMINI_MODEL`,
`CCMINI_WORKSPACE`, `CCMINI_BASE_URL`, `CCMINI_AUTO_APPROVE`, and `CCMINI_ALLOW_NETWORK`.

## Sessions, memory, and the REPL

Every run, one-shot or REPL, saves its transcript and a small distilled memory (the current
task, files touched, one note per turn) under `<workspace>/.ccmini/sessions/<id>.json`.
`--resume latest` or `--resume <id>` reloads one, so a follow-up run or the next time you
open the REPL in that repo picks the conversation back up, memory included. The startup
banner (the ASCII box above the prompt) always shows the session id in use, and the memory
is folded back into the model's context on every turn so it still knows what it was doing
even after old messages age out of the trimmed history (see `agent._make_pre_model_hook`).

Inside the REPL, lines starting with `/` are handled directly rather than sent to the model:

```
/help     show this list
/memory   print the session's distilled working memory
/session  print the path to this session's saved transcript
/reset    clear this session's history and memory (stays in the REPL)
/exit     leave the REPL (same as /quit, or plain 'exit')
```

## How it fits together

```
your task, plus saved session history and distilled memory  <- session.py, memory.py
   |
   v
create_react_agent  (the loop)        <- agent.py
   |  model proposes tool calls
   v
nine tools ------------------------>   <- tools.py, agent.py (run_subagent)
   |  read/list/search run freely
   |  write/edit/bash/web ask first -> permission gate   <- permissions.py
   |  every path checked         --->  workspace sandbox <- workspace.py
   |  run_bash confined (no net) --->  OS sandbox        <- sandbox.py
   |  web_fetch/web_search       --->  network (gated)   <- web.py
   |  run_subagent               --->  a second, bounded create_react_agent
   v
result goes back to the model, loop repeats until it answers, then the turn is
folded into memory and the session is saved                  <- cli.py
```

The model is provider-agnostic (`model.py`): the loop never names a provider, so any
LangChain chat model that supports tool calling drops in. `FakeToolModel` is the scripted
stand-in that makes the test suite and the demo run with no key and no network.

## Folder structure

```
cc-mini/
  CLAUDE.md             project context and module spec
  README.md             this file
  pyproject.toml
  docs/
    concepts.md         the agent loop, tools, and the permission idea
    design-decisions.md why langgraph, why nine tools, why a permission gate
  src/ccmini/           the package (config, workspace, permissions, sandbox, web, model,
                        tools, agent, memory, session, banner, cli)
  tests/                offline unit tests (FakeToolModel + a temp workspace)
  scripts/              the keyless demo
  sandbox/              throwaway dir the demo writes into (gitignored)
```

A workspace you point cc-mini at grows one runtime directory of its own,
`<workspace>/.ccmini/sessions/`, holding saved transcripts; gitignore it in projects you run
cc-mini against.

## A note on safety

`run_bash` is the one tool that can reach the rest of the machine, so it gets three layers.
It asks the permission gate first. On macOS (Seatbelt) and Linux (bubblewrap) it then runs
inside an OS sandbox that limits writes to the workspace and blocks the network, the same
shape Codex CLI and Claude Code use; pass `--allow-network` to let a command online. And the
shell's environment is scrubbed of likely secrets (`*_KEY`, `*_TOKEN`, `*_SECRET`, ...) so a
command cannot read your API keys. Two honest limits: where no sandbox is available (other
platforms, or bubblewrap not installed) `run_bash` is unconfined and the CLI says so at
startup, and `--yes` removes the permission prompt. Point cc-mini at a project directory you
are comfortable with, keep the gate on for anything unfamiliar, and read what it proposes
before you approve it.
