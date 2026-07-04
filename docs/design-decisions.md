# Design decisions

Each choice with the alternatives considered and why this one. Where a decision is
reversible, that is noted.

## Orchestration: LangGraph's prebuilt loop vs hand-rolled

Decision: use LangGraph's `create_react_agent`.

- Hand-roll the loop (a `while` over `model.invoke`, dispatch tool calls, append results).
  - Pros: nothing hidden, no framework dependency, the loop is right there to read. This is
    what the sibling `agentic-analyst` repo does, for good reasons in that project.
  - Cons: I would be reimplementing message bookkeeping, tool-call parsing, and the
    termination check that a maintained library already gets right, and the bugs live in my
    plumbing instead of someone else's tested code.
- LangGraph `create_react_agent`.
  - Pros: the loop is one well-tested function call, and it is what you would actually reach
    for to build this. It streams, it handles parallel tool calls, and it keeps the message
    state for me. The parts of cc-mini worth reading (the tools, the sandbox, the gate) stay
    in plain code I own.
  - Cons: the loop's mechanics are inside the library, not in front of you. `docs/concepts.md`
    spells them out so the idea is not lost to the abstraction.

The sibling repo hand-rolls its loop because the loop *is* its subject (a verification gate).
Here the subject is the tools and the safety model, not the loop, so leaning on the prebuilt
component is the honest call. This is reversible: the tools and the gate are plain functions,
so swapping in a hand-written loop later is mechanical.

## How many tools: six, grown to nine

Decision: read_file, list_files, search_files, write_file, edit_file, run_bash, plus
web_fetch, web_search, and run_subagent once a real need showed up for each.

A coding agent could expose dozens of tools (rename, move, git operations, apply-patch, a
language server). It could also expose just one, `run_bash`, and let the model do everything
through the shell. Six was the middle that kept the surface honest at first, and the rule
from the start was to grow it only when a real task proved something was missing, not
speculatively.

- `run_bash` alone would technically cover all of it, but then every action is an opaque
  command string. The harness cannot tell a read from a destructive write, so it cannot gate
  or sandbox them differently, and the model loses the structure that a typed `edit_file`
  gives it.
- A large toolset is more capable but harder to follow, and most of it is rarely used. The
  point of this repo is to be readable.

So: dedicated tools for the actions worth treating specially (reads that run freely, edits
that are gated and checked for a unique match), plus `run_bash` as the escape hatch for
everything else. Two things did prove a real need beyond the original six: locking `run_bash`
to no network (see "Safety" below) meant something had to fetch and search the web instead,
which is `web_fetch` and `web_search`; and a task that decomposes into one clean, bounded
subtask needed a way to delegate it without the top-level loop babysitting every step of it,
which is `run_subagent` (its own rationale is below, under "Bounded delegation"). Nine is
still small enough to read in one sitting; the rule stays the same for whatever comes next.

## Edits: unique string replacement vs line numbers or diffs

Decision: `edit_file(path, old, new)` replaces the first occurrence and refuses if `old` is
absent or appears more than once.

- Line-number edits ("replace lines 10 to 14") are brittle: the model's view of the file and
  the file on disk drift by a line and the edit lands in the wrong place.
- Full unified diffs are precise but fiddly for a model to emit correctly, and parsing them is
  more machinery than this repo wants.
- A unique-string replacement is what the model is good at: it reads the file, quotes the
  exact text it wants to change, and the tool refuses if that text is not unique, which forces
  the model to include enough context to be unambiguous. It is the same shape as Claude Code's
  editor tool, for the same reason.

The cost is that a change to text that genuinely repeats needs a longer `old` to disambiguate.
That is the right failure: better a refusal than an edit in the wrong place.

## Safety: a workspace sandbox and a permission gate

Decision: confine every path to one directory, and gate the five tools that change files or
reach the network (write_file, edit_file, run_bash, web_fetch, web_search).

- Trust the model (a careful system prompt, no enforcement).
  - Pros: no code.
  - Cons: it is advice, not a control. One confused step writes outside the project or runs
    something destructive, and there was nothing in the way.
- A sandbox plus a gate.
  - Pros: the sandbox makes "stay in this directory" an invariant the tools enforce, not a
    request. The gate makes "ask before changing things" a control that sits between the
    model's request and the action and cannot be prompted away. Both are small and tested
    directly.
  - Cons: a little friction (paths must be inside the workspace; mutations prompt unless you
    pass `--yes`). That friction is the feature.

This is the part of the project that is most worth keeping, so it stays even though it is the
least flashy.

## The gate's shape: a pure object with an injected ask

Decision: `Permissions` holds an `auto` flag and an `ask` callback, and logs every decision.

Putting the prompt behind a callback is what makes the gate testable. On the CLI `ask` is a
y/N prompt; in tests it is `lambda a, d: False` to deny or `True` to allow, with no terminal
involved. The log lets a test assert exactly what was asked and lets a real run be inspected
afterwards. Keeping the gate free of any model or network call means its behaviour is pinned
by fast unit tests, which is where you want your safety logic pinned.

## Provider: keyless local default, hosted optional

Decision: provider-agnostic via LangChain chat models, defaulting to a local open-weight model
served by Ollama, with OpenAI and Anthropic as optional extras and a scripted FakeToolModel
underneath.

- Default to a hosted model (Anthropic or OpenAI).
  - Pros: the strongest models, so the agent does the best work out of the box.
  - Cons: needs an API key and a card, and a key is exactly what we are trying to avoid here.
- Default to a local open-weight model via Ollama.
  - Pros: no key, no cost, nothing leaves the machine, and it matches the keyless-by-default
    convention in the sibling repos. The open-weight coding models are now good enough to drive
    a real tool loop.
  - Cons: needs Ollama installed and a model pulled, and quality depends on the model and the
    hardware. A 7B model will flail where a 30B will not.

Keyless local is the right default for a project whose whole appeal is that anyone can run it
without a key. The loop never names a provider, so a hosted model is `--provider anthropic`
(plus the extra and the key) when you want more capability. FakeToolModel sits under all of it
so the test suite and the demo run with no Ollama, no key, and no network, the role MockLLM
plays in the sibling repos.

## Which local model: tool calling is the gate

Decision: default to `qwen3-coder:30b`, and document lighter and heavier options.

The one non-negotiable requirement is tool calling. The agent is the model-tool-observe loop;
a model that cannot emit a tool call cannot drive it, no matter how well it writes code. That
rules out a lot of otherwise-good local models and is the first thing to check when picking one
(Ollama tags the capable ones "tools").

Among the models that clear that bar, `qwen3-coder:30b` is the best practical local pick: it is
tuned for agentic coding, it does tool calling reliably, and at roughly 18GB it runs on a strong
laptop or a single mid-range GPU. Smaller machines drop to `qwen2.5-coder:14b/7b` or
`llama3.1:8b` (more stable tool calling, weaker reasoning); bigger hardware can run `gpt-oss:120b`
or whatever newer tools-capable family has shipped. The default is one string in `config.py`, so
moving it as the local landscape changes is trivial. The README carries the current table.

## Bounded delegation: a second `create_react_agent`, not recursion

Decision: `run_subagent` wraps a second, independent `create_react_agent` built from the
same base tools minus itself, rather than letting the top-level agent call itself.

- Let the top-level agent's own tool list include `run_subagent` and hand it the same
  tools, including `run_subagent`.
  - Pros: one code path.
  - Cons: nothing stops a helper from delegating to a helper from delegating to a helper.
    "Bounded" delegation would not actually be bounded; a confused model could nest calls
    until the process runs out of stack or budget, which is exactly the runaway-loop shape
    the step cap exists to prevent everywhere else in this repo.
- Build the helper's tool list from `tools.build()` (the eight file/shell/web tools) with no
  `run_subagent` appended, so it is structurally impossible for a helper to delegate again.
  - Pros: bounded by construction, not by convention or a depth counter the model has to
    respect. The helper still shares the caller's `Workspace` and `Permissions`, so its
    mutations are gated the normal way; only the step budget (`max_subagent_steps`,
    default 15) is separate from the top-level `max_steps`.
  - Cons: a second `create_react_agent` object per run, built once in `agent.build` and
    reused across calls, which is a small, fixed cost.

This is the same instinct as the workspace sandbox and the permission gate: make the
safety property structural so it holds even when the model is confused, rather than asking
the model (or a prompt) to behave.

## Working memory: heuristic extraction, not a summarization call

Decision: `memory.update()` pulls the task, touched file paths, and one note per turn
straight from the messages a turn produced, with plain code, not by asking the model to
summarize itself.

- Ask the model to emit a summary each turn (an extra call, or a section of its answer).
  - Pros: a summary in the model's own words, potentially catching nuance heuristics miss.
  - Cons: another model call (cost and latency) or a fragile parse of part of the answer;
    and it makes memory non-deterministic, which would make `FakeToolModel` runs (the whole
    offline test suite) unable to pin what memory ends up holding without also scripting
    the summary text.
- Extract deterministically from tool-call arguments and the final answer text.
  - Pros: free (no extra call), and deterministic, so `tests/test_memory.py` can assert
    exact contents against a scripted turn. It also means memory injection into the prompt
    (via `agent._make_pre_model_hook`) never depends on the very model call it is feeding.
  - Cons: cruder than a real summary; a note is just the tool-call shape or the first ~100
    characters of the answer, not a synthesized "why".

Memory here is a small aid (what have we touched, what did we say we were doing), not a
second brain, so the cheap and deterministic version is the right size for what it is used
for.

## OpenAI-compatible local servers

Decision: let the `openai` provider take a `--base-url`, so it doubles as the path to any
OpenAI-compatible local server (vLLM, LM Studio, llama.cpp's server).

Ollama covers the easy keyless case, but plenty of people already run their open-weight models
behind an OpenAI-compatible endpoint. Rather than add a fourth provider, the openai branch
accepts a base URL and a placeholder key (local servers ignore the key), so those setups work
with the code that is already there. It is a few lines and broadens the keyless story without a
new dependency.
