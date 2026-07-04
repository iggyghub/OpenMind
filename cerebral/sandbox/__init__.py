"""Shell sandbox — Windows Job Object + AppContainer child execution (ADR-0010)."""
from cerebral.sandbox._interface import SandboxResult, Sandbox
from cerebral.sandbox._windows import WindowsSandbox


def available() -> bool:
    """True when a sandbox backend is usable on this host (fail-closed)."""
    return WindowsSandbox.available()


__all__ = ["SandboxResult", "Sandbox", "WindowsSandbox", "available"]
