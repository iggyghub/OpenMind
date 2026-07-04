"""Shell sandbox — Windows Job Object + AppContainer child execution (ADR-0010)."""
from cerebral.sandbox._interface import SandboxResult, Sandbox
from cerebral.sandbox._windows import WindowsSandbox

__all__ = ["SandboxResult", "Sandbox", "WindowsSandbox"]
