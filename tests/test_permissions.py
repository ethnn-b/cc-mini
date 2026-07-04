"""The permission gate is pure, so it is pinned directly."""

from ccmini.permissions import Permissions


def test_auto_allows_everything_and_logs():
    perms = Permissions(auto=True)
    decision = perms.check("write", "create foo.txt")
    assert decision.allowed
    assert perms.log == [("write", "create foo.txt", True)]


def test_ask_callback_decides():
    calls: list[tuple[str, str]] = []

    def ask(action: str, detail: str) -> bool:
        calls.append((action, detail))
        return action == "edit"  # allow edits, deny everything else

    perms = Permissions(auto=False, ask=ask)
    assert perms.check("edit", "foo.txt").allowed is True
    assert perms.check("run", "rm -rf /").allowed is False
    assert calls == [("edit", "foo.txt"), ("run", "rm -rf /")]


def test_denied_decision_has_reason():
    perms = Permissions(auto=False, ask=lambda a, d: False)
    decision = perms.check("write", "overwrite secrets")
    assert not decision.allowed
    assert "denied" in decision.reason.lower()
