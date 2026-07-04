"""The permission gate.

Read-only tools run freely; the five that change files or reach the network (write, edit,
bash, fetch, search) call Permissions.check first. In auto mode everything is allowed;
otherwise an `ask` callback decides (a y/N prompt on the CLI, a stub in tests). Every
decision is logged, so a run can be inspected and tests can assert what was asked.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass
class Decision:
    allowed: bool
    reason: str = ""


@dataclass
class Permissions:
    """Gate for mutating actions.

    auto=True allows everything (the --yes / trusted-context path). Otherwise `ask` is
    called with the action name and a one-line detail, and returns a bool.
    """

    auto: bool = False
    ask: Callable[[str, str], bool] | None = None
    log: list[tuple[str, str, bool]] = field(default_factory=list)

    def check(self, action: str, detail: str) -> Decision:
        if self.auto:
            self.log.append((action, detail, True))
            return Decision(True, "auto-approved")
        ask = self.ask or _console_ask
        allowed = ask(action, detail)
        self.log.append((action, detail, allowed))
        return Decision(allowed, "approved by user" if allowed else "denied by user")


def _console_ask(action: str, detail: str) -> bool:
    """Default gate: a y/N prompt on the terminal."""
    answer = input(f"\n  allow {action}? {detail}\n  [y/N] ").strip().lower()
    return answer in ("y", "yes")
