# Status

Updated: 2026-07-04
Phase: done
Progress: 15/15 milestones

## Milestones
- [x] Skeleton: config and workspace in place, importable
- [x] Permission gate, pure and unit-tested
- [x] The six tools, sandboxed to the workspace, unit-tested against a temp dir
- [x] FakeToolModel and the provider builder
- [x] Agent assembled with create_react_agent and the system prompt
- [x] End-to-end offline test (FakeToolModel scripts a write), green in pytest
- [x] CLI: one-shot and interactive REPL
- [x] Live provider wired (local Ollama default, keyless; OpenAI and Anthropic optional)
- [x] Keyless demo script that streams the loop
- [x] README with the novel angle and run commands
- [x] Session persistence and resume (session.py, `--resume latest|<id>`)
- [x] Distilled working memory, folded back into the prompt each turn (memory.py)
- [x] REPL slash commands (/help, /memory, /session, /reset, /exit, /quit)
- [x] Bounded subagent delegation (run_subagent, one level deep by construction)
- [x] Startup banner (banner.py) showing workspace, model, provider, approval, session, branch

## Current state
Working end to end. agent.build hands a chat model and the tools to LangGraph's
create_react_agent; agent.run invokes the graph on one task and returns the final text.
tools.build (tools.py) returns the core six plus the two web tools as closures over a
Workspace and a Permissions, so they are plain functions a test calls directly: the
read-only three (read_file, list_files, search_files) run freely, the five that write, run
a command, or reach the network (write_file, edit_file, run_bash, web_fetch, web_search)
call the gate first and return a "Denied" string the model reacts to. agent.build appends
run_subagent as the ninth. Every path is resolved through Workspace.resolve and refused if
it escapes the root.

model.build picks the provider (ollama default and keyless with qwen3-coder:30b, plus openai
and anthropic as lazy imports behind extras), so the default path is local open-weight models
with no key. The openai branch also takes a base_url, so it doubles as the route to any
OpenAI-compatible local server (vLLM, LM Studio, llama.cpp). FakeToolModel is the scripted
stand-in that drives a full pass through the loop with no Ollama, no key, and no network.
cli.py runs one-shot (a task argument) or an interactive REPL that carries the conversation
across turns, prints which provider, model, and workspace are in use plus a warning when the
gate is off, and turns a stopped-Ollama or unpulled-model failure into a one-line hint instead
of a traceback. Both modes stream: _stream_turn prints each tool call and its (clipped) result
as the loop turns, so the permission prompt fires between the call and its result the way
Claude Code shows it, then prints the final answer (or a clean step-limit note if cut off).

scripts/demo.py is the keyless demonstration: it points the agent at sandbox/, scripts the
model to write a fizzbuzz and run it, streams each tool call, and prints the permission log.
Confirmed running: it writes the file, runs it (the loop captures the 1..15 FizzBuzz output),
and summarises. scripts/demo_safety.py is the companion that shows the two safety boundaries
the happy path hides: a write outside the workspace refused by the path sandbox (before the
gate is even consulted), and a write inside it denied by the gate then allowed on retry, with
the permission log and a before/after check. Both confirmed running keyless.

Confirmed live against Ollama (llama3.1:8b, keyless): one-shot and REPL both stream the tool
calls, the y/N prompt gates writes mid-run (allow writes the file; deny returns "Denied" and
the model reports it made no change), and follow-up turns keep context. A weak tool-caller
occasionally passes a wrong arg name; the loop shows the tool error and the model recovers,
which is the loop working, not a bug. qwen2.5:32b is the steadier local tool-caller if the 8B
model fumbles.

## Hardening pass (Tier 1, post-review)
A brutal review against eight peer agents (Thorsten Ball's, mini-swe-agent, gptme, Aider,
SWE-agent ACI, Codex CLI, Claude Code) surfaced five gaps; all five are now closed:
- read_file returns line-numbered content with offset/limit paging and a "read with
  offset=N to continue" note, instead of a flat blob cut silently at 20k chars.
- search_files shells out to ripgrep (gitignore-aware, binary-skipping, parallel) with a
  guarded Python fallback that skips vendored/generated dirs and sniffs for binary files.
- run_bash is confined by an OS sandbox (new sandbox.py: macOS Seatbelt, Linux bubblewrap),
  network off by default (--allow-network to opt in), with secrets scrubbed from its env.
  This closes the hole where run_bash could write anywhere despite the path sandbox. The
  CLI warns at startup on platforms where no sandbox is available.
- The step cap fails gracefully: recursion_limit is 2*max_steps+1, GraphRecursionError is
  caught, and a cut-off loop returns a clear "stopped at the step limit" note instead of a
  traceback or a raw tool result presented as an answer.
- A pre_model_hook trims the messages each model call sees (trim_messages, last-N within a
  token budget) so a long run does not overflow or rot the context window.

## Web tools (post-review, two added by request)
The tool surface grew from six to eight. run_bash now has no network by default, so two
gated tools are the controlled way back online (the Codex/Claude Code split: no network in
the sandbox, a separate tool for the web). Both live in the new web.py, standard library
only, so the keyless path stays keyless:
- web_fetch(url): GET a URL and return its readable text (HTML reduced to visible text via
  a stdlib HTMLParser; scripts/styles dropped; truncated at the same 20k ceiling).
- web_search(query): titles, URLs, and snippets. Uses Tavily when TAVILY_API_KEY is set,
  otherwise a best-effort DuckDuckGo lite scrape (unofficial and fragile by nature; a key is
  the better path when search matters). Confirmed working live against both fetch and search.
Both are permission-gated (actions "fetch" and "search"), so a denied call returns "Denied"
before any network request happens.

## Gap-closing pass (parity with rasbt/mini-coding-agent)
A comparison against rasbt/mini-coding-agent turned up four things it has that cc-mini did
not: session persistence/resume, a distilled working memory, REPL slash commands, and
bounded subagent delegation. All four are in, plus a startup banner UI the user asked for
separately:
- session.py: a run's transcript and memory are saved to
  `<workspace>/.ccmini/sessions/<id>.json` after every turn (hand-rolled message
  (de)serialization, not a langchain internal helper, so the format stays stable across
  langchain versions). `--resume latest` or `--resume <id>` reloads one.
- memory.py: a `Memory` (task, files touched, one note per turn) updated heuristically from
  tool-call args and the final answer text, not by an extra summarization call, so it stays
  deterministic under FakeToolModel. agent._make_pre_model_hook folds it into every model
  call as a SystemMessage that is never itself stored in the transcript, so it cannot pile
  up across turns.
- cli.py REPL: `/help`, `/memory`, `/session`, `/reset`, `/exit`, `/quit`, handled before a
  line ever reaches the model.
- agent.py: run_subagent, the ninth tool, wraps a second create_react_agent built from the
  base tools minus itself, so delegation is exactly one level deep by construction,
  not by a depth counter the model has to respect. Shares the caller's Workspace and
  Permissions, so a helper's own mutations are still gated normally; only its step budget
  (`max_subagent_steps`, default 15) is separate.
- banner.py: an ASCII box (WORKSPACE, MODEL, PROVIDER, APPROVAL, SESSION, BRANCH) printed at
  the top of every run, one-shot or REPL.
Confirmed live against Ollama (qwen2.5:32b): a one-shot run showed the banner and saved a
session; `--resume latest` correctly recalled the prior turn's request; `/help`, `/memory`,
and `/session` behaved correctly piped into the REPL; and a delegated run_subagent call
returned the helper's answer to the top-level model, which reported it back accurately.

## Tests
54 passing offline with no key and no network (pytest over tests/). The 22 newest: session
save/load roundtrip and latest() (test_session.py); memory extraction, dedup/reorder on a
retouched file, and the notes/files caps (test_memory.py); the banner's content and its
git-branch lookup on a non-repo directory (test_banner.py); one end-to-end delegation test
where the same FakeToolModel instance plays both the top-level and the nested subagent call
(test_agent.py); and the REPL's slash commands, /reset actually clearing state, and a
resumed session reloading prior messages (test_cli.py). On top of the original 32: the CLI
streaming display, read_file paging and line numbers, empty-file and past-end handling, the
search fallback skipping generated and binary files, run_bash confined to the workspace
(skipped where no OS sandbox), secret scrubbing, the step cap returning a clear note instead
of crashing, and the two web tools (HTML-to-text, non-http rejection, gating, and DuckDuckGo
result parsing) exercised offline with the HTTP layer monkeypatched.

create_react_agent is deprecated in LangGraph 1.0 (moves to langchain.agents.create_agent in
2.0); it still works on the installed 1.2.7, and the one deprecation warning is scoped-silenced
in pyproject so the suite output is clean. See the comment in agent.py for the swap when it is
removed.

## Blockers
None.

## Next
Optional polish, not required for the end-to-end path:
1. Tier 2 from the review: a non-mutating update_plan (TODO) tool, edit_file replace_all +
   informative errors, and a diff preview at the permission prompt.
2. Token-level streaming (print the model's text as it is generated, not per message) if the
   per-message stream ever feels laggy on a slow local model.
3. A saved-session list command (e.g. `/sessions` or `cc-mini --list-sessions`) if juggling
   more than a couple of resumable sessions per workspace turns out to be common.

Done since last update: CLI streaming and the gated web tools were already shipped (see the
prior entries above); a live smoke test against Ollama is done ad hoc each pass (most
recently in the gap-closing pass above) rather than landed as an automated test, on purpose,
so the default suite stays keyless.
