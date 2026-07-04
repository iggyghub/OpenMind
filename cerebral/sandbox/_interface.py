"""Thin Sandbox interface seam — platform impls fulfil this contract."""
from __future__ import annotations
import abc
from dataclasses import dataclass
from typing import Optional


@dataclass
class SandboxResult:
    stdout: str
    stderr: str
    exit_code: int
    killed_reason: Optional[str] = None  # "wall_clock" | None


class Sandbox(abc.ABC):
    """Platform sandbox backend.  spawn() is the only required surface."""

    @classmethod
    def available(cls) -> bool:
        """True when this backend is usable on the current host. Fail-closed default."""
        return False

    @abc.abstractmethod
    def spawn(
        self,
        cmd: list[str],
        workdir: str,
        *,
        timeout_s: Optional[float] = None,
    ) -> SandboxResult:
        """Run *cmd* inside the sandbox, return when done or killed."""
