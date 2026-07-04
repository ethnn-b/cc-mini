"""The eight file, shell, and web tools (run_subagent, the ninth, lives in agent.py).

Read-only tools run without asking; the rest call the permission gate first and return
"Denied" rather than raising, so the agent adapts instead of crashing. Every path goes
through Workspace.resolve. Built as closures over a Workspace and a Permissions, so each
tool is a plain function a test can call directly.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

from langchain_core.tools import BaseTool, tool

from ccmini import sandbox, web
from ccmini.permissions import Permissions
from ccmini.workspace import Workspace

# Caps so a single tool result cannot flood the model's context.
MAX_OUTPUT_CHARS = 20_000
MAX_SEARCH_HITS = 100
BASH_TIMEOUT_SECONDS = 120
# read_file pages by line, like Claude Code's Read: a default window and a hard ceiling
# on how much one call can return, so the model pages large files instead of being cut off.
READ_DEFAULT_LINES = 1500
# Directories the Python search fallback skips (generated / vendored). ripgrep handles
# this itself via .gitignore, so this only matters when rg is absent.
_SEARCH_SKIP_DIRS = frozenset({
    ".git", ".hg", ".svn", ".venv", "venv", "env", "node_modules", "__pycache__",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox", "dist", "build",
    ".idea", ".vscode", "site-packages", ".eggs", "target",
})


def _truncate(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [truncated, {len(text) - limit} more chars]"


def build(
    workspace: Workspace, permissions: Permissions, allow_network: bool = False
) -> list[BaseTool]:
    """Return the eight file, shell, and web tools, bound to this workspace and permission gate.

    allow_network lets run_bash reach the network; it is off by default so a shell
    command cannot phone home unless the user opts in.
    """

    @tool
    def read_file(path: str, offset: int = 1, limit: int = READ_DEFAULT_LINES) -> str:
        """Read a text file and return its contents with line numbers (`lineno<tab>text`).
        Use this before editing a file so your `old` text matches the exact text on disk.

        `path` is relative to the project root. `offset` is the 1-based first line to read
        and `limit` is how many lines to return; a long file comes back one page at a time,
        with a note telling you the next `offset` to continue from. Quote text without the
        line-number prefix when you edit."""
        try:
            full = workspace.resolve(path)
        except ValueError as exc:
            return f"Error: {exc}"
        if not full.is_file():
            return f"Error: no such file: {path}"
        try:
            text = full.read_text()
        except UnicodeDecodeError:
            return f"Error: {path} is not a text file"
        lines = text.splitlines()
        total = len(lines)
        if total == 0:
            return "(empty file)"
        if offset < 1:
            offset = 1
        if offset > total:
            return f"Error: offset {offset} is past the end of {path} ({total} lines)"
        end = min(offset - 1 + max(limit, 1), total)
        width = len(str(end))
        body = "\n".join(f"{n:>{width}}\t{lines[n - 1]}" for n in range(offset, end + 1))
        if end < total:
            body += (
                f"\n... [showing lines {offset}-{end} of {total}; "
                f"read with offset={end + 1} to continue]"
            )
        return _truncate(body)

    @tool
    def list_files(path: str = ".") -> str:
        """List the files and directories directly under `path` (default: project root).
        Directories are shown with a trailing slash. Use this to get your bearings."""
        try:
            full = workspace.resolve(path)
        except ValueError as exc:
            return f"Error: {exc}"
        if not full.is_dir():
            return f"Error: not a directory: {path}"
        entries = sorted(full.iterdir(), key=lambda p: (p.is_file(), p.name))
        if not entries:
            return "(empty)"
        lines = [f"{p.name}/" if p.is_dir() else p.name for p in entries]
        return "\n".join(lines)

    @tool
    def search_files(pattern: str, path: str = ".") -> str:
        """Search file contents for a regular expression, like grep. Returns matching
        lines as `relative/path:line: text`. Use this to find where something is defined
        or used. `path` scopes the search (default: whole project). Generated and vendored
        directories (.git, node_modules, .venv, ...) are skipped."""
        try:
            root = workspace.resolve(path)
        except ValueError as exc:
            return f"Error: {exc}"
        if shutil.which("rg"):
            return _search_with_ripgrep(pattern, root)
        return _search_with_python(pattern, root)

    def _search_with_ripgrep(pattern: str, root: Path) -> str:
        # ripgrep respects .gitignore, skips binaries, and is parallel. -uu would override
        # that; we want the gitignore-aware default. Paths print relative to the search dir.
        proc = subprocess.run(
            ["rg", "--line-number", "--no-heading", "--color=never",
             "--max-count", str(MAX_SEARCH_HITS), "-e", pattern, "."],
            cwd=root if root.is_dir() else root.parent,
            capture_output=True, text=True, timeout=BASH_TIMEOUT_SECONDS,
        )
        if proc.returncode == 1 and not proc.stdout:
            return "(no matches)"
        if proc.returncode >= 2:
            return f"Error: {proc.stderr.strip() or 'search failed'}"
        lines = proc.stdout.splitlines()
        out = [f"{ln.rstrip()}" for ln in lines[:MAX_SEARCH_HITS]]
        if len(lines) > MAX_SEARCH_HITS:
            out.append(f"... [stopped at {MAX_SEARCH_HITS} matches]")
        return "\n".join(out) if out else "(no matches)"

    def _search_with_python(pattern: str, root: Path) -> str:
        try:
            regex = re.compile(pattern)
        except re.error as exc:
            return f"Error: {exc}"
        files = [root] if root.is_file() else _walk_searchable(root)
        hits: list[str] = []
        for file in files:
            try:
                with open(file, "rb") as fh:
                    if b"\x00" in fh.read(8192):  # binary sniff, skip without decoding all
                        continue
                text = file.read_text()
            except (UnicodeDecodeError, OSError):
                continue
            rel = workspace.relative(file)
            for n, line in enumerate(text.splitlines(), start=1):
                if regex.search(line):
                    hits.append(f"{rel}:{n}: {line.strip()}")
                    if len(hits) >= MAX_SEARCH_HITS:
                        hits.append(f"... [stopped at {MAX_SEARCH_HITS} matches]")
                        return "\n".join(hits)
        return "\n".join(hits) if hits else "(no matches)"

    def _walk_searchable(root: Path):
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in _SEARCH_SKIP_DIRS]
            for name in filenames:
                yield Path(dirpath) / name

    @tool
    def write_file(path: str, content: str) -> str:
        """Create a new file or overwrite an existing one with `content`. Asks for
        permission first. Parent directories are created as needed."""
        try:
            full = workspace.resolve(path)
        except ValueError as exc:
            return f"Error: {exc}"
        verb = "overwrite" if full.exists() else "create"
        decision = permissions.check("write", f"{verb} {path} ({len(content)} chars)")
        if not decision.allowed:
            return f"Denied: {decision.reason}"
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content)
        return f"Wrote {path} ({len(content)} chars)"

    @tool
    def edit_file(path: str, old: str, new: str) -> str:
        """Replace the first exact occurrence of `old` with `new` in a file. Asks for
        permission first. Read the file beforehand so `old` matches the text exactly; the
        edit is refused if `old` is absent or appears more than once."""
        try:
            full = workspace.resolve(path)
        except ValueError as exc:
            return f"Error: {exc}"
        if not full.is_file():
            return f"Error: no such file: {path}"
        text = full.read_text()
        count = text.count(old)
        if count == 0:
            return f"Error: `old` text not found in {path}"
        if count > 1:
            return f"Error: `old` text appears {count} times in {path}; make it unique"
        decision = permissions.check("edit", f"{path} (replace {len(old)} chars)")
        if not decision.allowed:
            return f"Denied: {decision.reason}"
        full.write_text(text.replace(old, new, 1))
        return f"Edited {path}"

    @tool
    def run_bash(command: str) -> str:
        """Run a shell command in the project root and return its combined output and exit
        code. Asks for permission first. Use this to run tests, check git status, or
        inspect the project. On supported platforms the command is confined to the
        workspace and has no network access (pass --allow-network to enable it)."""
        decision = permissions.check("run", command)
        if not decision.allowed:
            return f"Denied: {decision.reason}"
        argv = sandbox.wrap(command, workspace.root, allow_network=allow_network)
        try:
            result = subprocess.run(
                argv,
                cwd=workspace.root,
                capture_output=True,
                text=True,
                timeout=BASH_TIMEOUT_SECONDS,
                env=sandbox.scrub_env(dict(os.environ)),
            )
        except subprocess.TimeoutExpired:
            return f"Error: command timed out after {BASH_TIMEOUT_SECONDS}s"
        output = (result.stdout or "") + (result.stderr or "")
        return _truncate(f"(exit {result.returncode})\n{output}".rstrip())

    @tool
    def web_fetch(url: str) -> str:
        """Fetch a URL and return its readable text (HTML is reduced to plain text). Asks
        for permission first, since it reaches the network. Use this to read documentation,
        a changelog, or any page when you already have the link."""
        decision = permissions.check("fetch", url)
        if not decision.allowed:
            return f"Denied: {decision.reason}"
        return web.fetch(url)

    @tool
    def web_search(query: str) -> str:
        """Search the web and return result titles, URLs, and snippets. Asks for permission
        first. Use this to find documentation or solutions when you do not have a URL; then
        web_fetch the most relevant result to read it in full."""
        decision = permissions.check("search", query)
        if not decision.allowed:
            return f"Denied: {decision.reason}"
        return web.search(query)

    return [
        read_file, list_files, search_files,
        write_file, edit_file, run_bash,
        web_fetch, web_search,
    ]
