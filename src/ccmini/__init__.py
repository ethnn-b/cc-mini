"""cc-mini: a tiny coding agent.

An LLM runs in a LangGraph loop with nine tools for reading, editing, running commands,
reaching the web, and delegating bounded subtasks. Mutating and network tools pass through
a permission gate, and every file path is confined to one workspace directory. Small
enough to read in a sitting, which is the point.
"""

from ccmini.config import Config

__all__ = ["Config"]
