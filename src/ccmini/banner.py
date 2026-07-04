"""The startup banner: an ASCII box printed once at the top of a run, so cc-mini's state
is visible at a glance.

Plain ASCII (`+`, `-`, `=`, `|`) rather than Unicode box-drawing, so it renders in any
terminal font. `render()` takes only what `cli.main` already has on hand (Config, Workspace,
the session id); the git branch lookup is the one bit of I/O, and it fails quiet, so no
git, no repo, or a slow filesystem just omits the BRANCH row.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from ccmini.config import Config
from ccmini.workspace import Workspace

WIDTH = 74
LEFT_COL = 40

MASCOT = [
    r"      _______      ",
    r"     /   o   \     ",
    r"    |  o   o  |    ",
    r"    |   ___   |    ",
    r"     \_______/     ",
]


def _rule(char: str) -> str:
    return "+" + char * WIDTH + "+"


def _center(text: str) -> str:
    return "|" + text.center(WIDTH) + "|"


def _row(label: str, value: str) -> str:
    """A one-column row. A value long enough to overflow the box (a deep workspace path)
    is shown in full rather than truncated; the row's right edge just moves for that one
    line, since a truncated path is worse than a ragged border."""
    text = f" {label:<10}{value}"
    if len(text) >= WIDTH:
        return "|" + text + "|"
    return "|" + text.ljust(WIDTH) + "|"


def _row2(label1: str, value1: str, label2: str, value2: str) -> str:
    left = f" {label1:<10}{value1}"[:LEFT_COL].ljust(LEFT_COL)
    right = f"{label2:<10}{value2}"
    text = (left + right)[:WIDTH]
    return "|" + text.ljust(WIDTH) + "|"


def _git_branch(root: Path) -> str:
    """The current branch name, or "" if `root` is not a git repo, git is missing, or the
    lookup is slow (a network-mounted or huge repo should never hold up the banner)."""
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            timeout=1,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def render(cfg: Config, workspace: Workspace, session_id: str) -> str:
    """The full banner: mascot, then a WORKSPACE/MODEL/PROVIDER/APPROVAL/SESSION/BRANCH
    fact table, built entirely from the run's own config and workspace."""
    approval = "auto" if cfg.auto_approve else "ask"
    lines = [_rule("=")]
    lines.extend(_center(line) for line in MASCOT)
    lines.append(_center("CC-MINI"))
    lines.append(_rule("-"))
    lines.append(_row("WORKSPACE", str(workspace.root)))
    lines.append(_row2("MODEL", cfg.resolved_model(), "PROVIDER", cfg.provider))
    lines.append(_row2("APPROVAL", approval, "SESSION", session_id))
    branch = _git_branch(workspace.root)
    if branch:
        lines.append(_row("BRANCH", branch))
    lines.append(_rule("="))
    return "\n".join(lines)
