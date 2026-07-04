"""The startup banner, offline. render() is a pure string builder over Config/Workspace;
the one bit of I/O (_git_branch) is exercised against a plain temp dir that is not a git
repo, so no network and no dependency on this machine's git state.
"""

from ccmini import banner
from ccmini.config import Config
from ccmini.workspace import Workspace


def test_render_shows_workspace_model_and_session(tmp_path):
    cfg = Config(workspace=str(tmp_path), provider="ollama", model="qwen3-coder:30b")
    workspace = Workspace(tmp_path)

    text = banner.render(cfg, workspace, "20260704-101530-abcdef")

    assert str(workspace.root) in text
    assert "qwen3-coder:30b" in text
    assert "ollama" in text
    assert "20260704-101530-abcdef" in text
    assert "ask" in text  # default approval mode


def test_render_shows_auto_approval_when_yes_is_set(tmp_path):
    cfg = Config(workspace=str(tmp_path), auto_approve=True)

    text = banner.render(cfg, Workspace(tmp_path), "sid")

    assert "auto" in text


def test_git_branch_on_a_non_git_directory_is_empty(tmp_path):
    assert banner._git_branch(tmp_path) == ""


def test_render_omits_branch_row_outside_a_git_repo(tmp_path):
    cfg = Config(workspace=str(tmp_path))

    text = banner.render(cfg, Workspace(tmp_path), "sid")

    assert "BRANCH" not in text
