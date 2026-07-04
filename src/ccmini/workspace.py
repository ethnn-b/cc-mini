"""The path sandbox.

Every tool resolves the model's path argument through Workspace.resolve before touching
the disk. A path that escapes the root (via .., an absolute path, or a symlink pointing
outside) raises, so the agent can only read and write inside the one directory you pointed
it at. Kept tiny and tested directly, since it's the one safety boundary the file tools
rely on.
"""

from __future__ import annotations

from pathlib import Path


class PathEscapeError(ValueError):
    """Raised when a requested path resolves outside the workspace root."""


class Workspace:
    """A directory the tools are confined to."""

    def __init__(self, root: str | Path = "."):
        self.root = Path(root).resolve()

    def resolve(self, path: str) -> Path:
        """Resolve `path` (relative to the root, or absolute) and confirm it stays inside.

        Resolving first, then checking, defeats .. traversal and symlinks that would
        otherwise point out of the workspace.
        """
        candidate = Path(path)
        full = candidate if candidate.is_absolute() else self.root / candidate
        full = full.resolve()
        if full != self.root and self.root not in full.parents:
            raise PathEscapeError(f"path {path!r} resolves outside the workspace {self.root}")
        return full

    def relative(self, path: Path) -> str:
        """Path shown to the model: relative to the root, so it sees workspace-local names."""
        try:
            return str(path.relative_to(self.root)) or "."
        except ValueError:
            return str(path)
