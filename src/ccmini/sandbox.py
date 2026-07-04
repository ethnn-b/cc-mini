"""OS-level confinement for run_bash.

The path sandbox (workspace.py) only governs the file tools; run_bash spawns a real shell,
which on its own could read, write, and reach the network anywhere the user can. This wraps
the command in the platform's own sandbox (macOS Seatbelt via sandbox-exec, Linux bubblewrap)
so it is confined to the workspace and has no network access by default, while shell
features (pipes, &&, globs, redirects) still work under /bin/sh.

Where no sandbox tool is available, wrap() falls back to a plain /bin/sh and the CLI warns
at startup that run_bash is unconfined; the permission gate still applies either way.
"""

from __future__ import annotations

import platform
import shutil
from pathlib import Path

# Substrings that mark an environment variable as a likely secret. Such variables are
# dropped from the shell command's environment so a command (or a prompt-injected one)
# cannot read or exfiltrate the user's API keys. The agent's own model calls happen in
# the parent process, so run_bash never needs these.
_SECRET_HINTS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "CREDENTIAL", "AWS_ACCESS")


def scrub_env(env: dict[str, str]) -> dict[str, str]:
    """Return a copy of `env` with likely-secret variables removed."""
    return {k: v for k, v in env.items() if not any(h in k.upper() for h in _SECRET_HINTS)}


def _is_macos() -> bool:
    return platform.system() == "Darwin" and shutil.which("sandbox-exec") is not None


def _is_linux_bwrap() -> bool:
    return platform.system() == "Linux" and shutil.which("bwrap") is not None


def available() -> bool:
    """True if this platform can confine run_bash to the workspace."""
    return _is_macos() or _is_linux_bwrap()


def _seatbelt_profile(root: Path, allow_network: bool) -> str:
    """A macOS Seatbelt profile: read anywhere, write only inside the workspace (plus the
    temp dirs and the standard /dev sinks), and no network unless explicitly allowed."""
    lines = ["(version 1)", "(allow default)"]
    if not allow_network:
        lines.append("(deny network*)")
    lines.append("(deny file-write*)")
    lines.append(
        f'(allow file-write* (subpath "{root}")'
        ' (subpath "/private/tmp") (subpath "/private/var/folders") (subpath "/tmp")'
        ' (literal "/dev/null") (literal "/dev/stdout") (literal "/dev/stderr")'
        ' (literal "/dev/dtracehelper") (literal "/dev/tty"))'
    )
    return "\n".join(lines)


def wrap(command: str, root: Path, allow_network: bool = False) -> list[str]:
    """Return an argv (for subprocess.run with shell=False) that runs `command` under
    /bin/sh, confined to `root` with network off unless allow_network is set.

    Falls back to an unconfined /bin/sh -c where no sandbox is available.
    """
    root = Path(root)
    if _is_macos():
        profile = _seatbelt_profile(root, allow_network)
        return ["sandbox-exec", "-p", profile, "/bin/sh", "-c", command]
    if _is_linux_bwrap():
        argv = [
            "bwrap",
            "--ro-bind", "/", "/",
            "--bind", str(root), str(root),
            "--bind", "/tmp", "/tmp",
            "--dev", "/dev",
            "--proc", "/proc",
            "--chdir", str(root),
        ]
        if not allow_network:
            argv.append("--unshare-net")
        argv += ["/bin/sh", "-c", command]
        return argv
    return ["/bin/sh", "-c", command]
