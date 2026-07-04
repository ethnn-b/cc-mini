"""The tools, exercised against a temp workspace.

build() returns LangChain tools; .invoke({...}) calls one the way the agent does. These
tests confirm the read/write/edit/search behaviour, the permission gate on mutations, and
the path sandbox.
"""

import os

import pytest

import ccmini.tools as toolmod
from ccmini import sandbox
from ccmini.permissions import Permissions
from ccmini.tools import build
from ccmini.workspace import PathEscapeError, Workspace


def _tools(tmp_path, auto=True, ask=None):
    workspace = Workspace(tmp_path)
    perms = Permissions(auto=auto, ask=ask)
    by_name = {t.name: t for t in build(workspace, perms)}
    return by_name, perms


def test_write_then_read_roundtrip(tmp_path):
    tools, _ = _tools(tmp_path)
    out = tools["write_file"].invoke({"path": "hello.txt", "content": "hi there"})
    assert "Wrote hello.txt" in out
    assert (tmp_path / "hello.txt").read_text() == "hi there"
    # read_file returns line-numbered content (lineno<tab>text), like Claude Code's Read.
    assert tools["read_file"].invoke({"path": "hello.txt"}) == "1\thi there"


def test_read_paginates_large_file_with_offset(tmp_path):
    (tmp_path / "big.txt").write_text("".join(f"line {i}\n" for i in range(1, 21)))
    tools, _ = _tools(tmp_path)
    page = tools["read_file"].invoke({"path": "big.txt", "offset": 1, "limit": 5})
    assert page.startswith("1\tline 1")
    assert "5\tline 5" in page
    assert "offset=6 to continue" in page  # tells the model how to page on
    rest = tools["read_file"].invoke({"path": "big.txt", "offset": 6, "limit": 100})
    assert "6\tline 6" in rest
    assert "continue" not in rest  # last page carries no more-data note


def test_read_empty_file(tmp_path):
    (tmp_path / "e.txt").write_text("")
    tools, _ = _tools(tmp_path)
    assert tools["read_file"].invoke({"path": "e.txt"}) == "(empty file)"


def test_read_offset_past_end_is_an_error(tmp_path):
    (tmp_path / "a.txt").write_text("one\ntwo\n")
    tools, _ = _tools(tmp_path)
    assert "past the end" in tools["read_file"].invoke({"path": "a.txt", "offset": 99})


def test_read_missing_file_returns_error_not_raises(tmp_path):
    tools, _ = _tools(tmp_path)
    assert "no such file" in tools["read_file"].invoke({"path": "nope.txt"})


def test_edit_replaces_unique_occurrence(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\ny = 2\n")
    tools, _ = _tools(tmp_path)
    out = tools["edit_file"].invoke({"path": "a.py", "old": "y = 2", "new": "y = 3"})
    assert "Edited a.py" in out
    assert (tmp_path / "a.py").read_text() == "x = 1\ny = 3\n"


def test_edit_refuses_ambiguous_match(tmp_path):
    (tmp_path / "a.py").write_text("v = 1\nv = 1\n")
    tools, _ = _tools(tmp_path)
    out = tools["edit_file"].invoke({"path": "a.py", "old": "v = 1", "new": "v = 2"})
    assert "appears 2 times" in out
    assert (tmp_path / "a.py").read_text() == "v = 1\nv = 1\n"  # untouched


def test_list_files(tmp_path):
    (tmp_path / "a.txt").write_text("x")
    (tmp_path / "sub").mkdir()
    tools, _ = _tools(tmp_path)
    out = tools["list_files"].invoke({"path": "."})
    assert "a.txt" in out
    assert "sub/" in out


def test_search_files(tmp_path):
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n")
    (tmp_path / "b.py").write_text("x = foo()\n")
    tools, _ = _tools(tmp_path)
    out = tools["search_files"].invoke({"pattern": r"foo", "path": "."})
    assert "a.py:1" in out
    assert "b.py:1" in out


def test_search_fallback_skips_generated_dirs(tmp_path, monkeypatch):
    # Force the Python fallback (pretend ripgrep is absent) and confirm it does not
    # descend into vendored/generated directories the way the old naive walk did.
    monkeypatch.setattr(toolmod.shutil, "which", lambda name: None)
    (tmp_path / "src.py").write_text("needle = 1\n")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "dep.py").write_text("needle = 2\n")
    tools, _ = _tools(tmp_path)
    out = tools["search_files"].invoke({"pattern": "needle", "path": "."})
    assert "src.py" in out
    assert "node_modules" not in out


def test_search_fallback_skips_binary(tmp_path, monkeypatch):
    monkeypatch.setattr(toolmod.shutil, "which", lambda name: None)
    (tmp_path / "bin.dat").write_bytes(b"needle\x00\x00rest")
    (tmp_path / "ok.txt").write_text("needle here\n")
    tools, _ = _tools(tmp_path)
    out = tools["search_files"].invoke({"pattern": "needle", "path": "."})
    assert "ok.txt" in out
    assert "bin.dat" not in out


def test_run_bash_captures_output(tmp_path):
    tools, _ = _tools(tmp_path)
    out = tools["run_bash"].invoke({"command": "echo hello"})
    assert "hello" in out
    assert "exit 0" in out


@pytest.mark.skipif(not sandbox.available(), reason="no OS sandbox on this platform")
def test_run_bash_is_confined_to_the_workspace(tmp_path):
    # A shell command cannot write outside the workspace when the OS sandbox is available,
    # closing the hole that run_bash otherwise opens through the path boundary.
    tools, _ = _tools(tmp_path)
    target = os.path.expanduser("~/cc_mini_pytest_escape.txt")
    try:
        tools["run_bash"].invoke({"command": f"echo pwned > {target}"})
        assert not os.path.exists(target)
        # writing inside the workspace still works
        out = tools["run_bash"].invoke({"command": "echo ok > inside.txt && cat inside.txt"})
        assert "ok" in out
        assert (tmp_path / "inside.txt").exists()
    finally:
        if os.path.exists(target):
            os.remove(target)


def test_run_bash_scrubs_secrets_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("MY_API_KEY", "super-secret")
    tools, _ = _tools(tmp_path)
    out = tools["run_bash"].invoke({"command": "echo key=[${MY_API_KEY}]"})
    assert "super-secret" not in out
    assert "key=[]" in out


def test_mutations_are_gated(tmp_path):
    # Deny everything; the read-only tools still work, the mutating ones do not.
    tools, perms = _tools(tmp_path, auto=False, ask=lambda a, d: False)
    assert "Denied" in tools["write_file"].invoke({"path": "x.txt", "content": "no"})
    assert not (tmp_path / "x.txt").exists()
    assert "Denied" in tools["run_bash"].invoke({"command": "echo hi"})
    # Three mutation attempts were asked about; reads never reach the gate.
    tools["list_files"].invoke({"path": "."})
    assert [a for a, _, _ in perms.log] == ["write", "run"]


def test_web_fetch_reduces_html_to_text_and_drops_scripts(tmp_path, monkeypatch):
    import ccmini.web as webmod
    page = (
        "<html><head><title>t</title><style>p{color:red}</style></head>"
        "<body><p>Hello</p><script>steal()</script><p>World</p></body></html>"
    )
    monkeypatch.setattr(webmod, "_get", lambda url, **kw: ("text/html", page))
    tools, _ = _tools(tmp_path)
    out = tools["web_fetch"].invoke({"url": "https://example.com"})
    assert "Hello" in out and "World" in out
    assert "steal()" not in out  # script content is not returned
    assert "color:red" not in out  # style content is not returned


def test_web_fetch_rejects_non_http_url(tmp_path):
    tools, _ = _tools(tmp_path)
    out = tools["web_fetch"].invoke({"url": "file:///etc/passwd"})
    assert "http" in out.lower()


def test_web_fetch_is_gated(tmp_path):
    tools, perms = _tools(tmp_path, auto=False, ask=lambda a, d: False)
    assert "Denied" in tools["web_fetch"].invoke({"url": "https://example.com"})
    assert [a for a, _, _ in perms.log] == ["fetch"]  # gated before any network call


def test_web_search_is_gated(tmp_path):
    tools, perms = _tools(tmp_path, auto=False, ask=lambda a, d: False)
    assert "Denied" in tools["web_search"].invoke({"query": "python asyncio"})
    assert [a for a, _, _ in perms.log] == ["search"]


def test_web_search_formats_duckduckgo_results(tmp_path, monkeypatch):
    import ccmini.web as webmod
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    html = (
        '<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fdocs.python.org%2F">'
        "Python <b>Docs</b></a>"
        '<a class="result__snippet">The official documentation.</a>'
    )
    monkeypatch.setattr(webmod, "_get", lambda url, **kw: ("text/html", html))
    tools, _ = _tools(tmp_path)
    out = tools["web_search"].invoke({"query": "python docs"})
    assert "Python Docs" in out  # nested <b> did not truncate the title
    assert "https://docs.python.org/" in out  # the uddg redirect was unwrapped
    assert "official documentation" in out


def test_path_escape_is_blocked(tmp_path):
    tools, _ = _tools(tmp_path)
    out = tools["read_file"].invoke({"path": "../../etc/passwd"})
    assert "outside the workspace" in out


def test_workspace_resolve_raises_on_escape(tmp_path):
    ws = Workspace(tmp_path)
    with pytest.raises(PathEscapeError):
        ws.resolve("../secret")
    inside = ws.resolve("sub/file.txt")
    assert str(inside).startswith(str(tmp_path))
