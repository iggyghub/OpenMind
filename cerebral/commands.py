"""Command registry -- deterministic dispatch without a model turn (harness
parity H4-S1 / #735, dsh's ctx.commands).

A plugin/module registers a Command (exact wake phrases or /name syntax); a
match in _process_command bypasses the planner/ChainEngine entirely -- fast,
deterministic, free, no model mis-parse risk. No match = zero behavior change
(falls through to the existing LLM path unchanged).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable


@dataclass
class Command:
    name: str
    phrases: tuple[str, ...]
    handler: Callable[[], Awaitable[object]]
    capability: str | None = None


class CommandRegistry:
    """Exact-match registry: a case-insensitive phrase, or /name syntax."""

    def __init__(self) -> None:
        self._commands: dict[str, Command] = {}
        self._phrase_index: dict[str, str] = {}

    def register(self, command: Command) -> None:
        # Re-registering a name replaces cleanly -- drop that name's old
        # phrases from the index first so a stale phrase can't still match.
        old = self._commands.get(command.name)
        if old is not None:
            for phrase in old.phrases:
                if self._phrase_index.get(phrase.strip().lower()) == command.name:
                    del self._phrase_index[phrase.strip().lower()]
        self._commands[command.name] = command
        for phrase in command.phrases:
            self._phrase_index[phrase.strip().lower()] = command.name

    def match(self, text: str) -> "Command | None":
        text = (text or "").strip()
        if not text:
            return None
        if text.startswith("/"):
            name = text[1:].split(" ", 1)[0].strip().lower()
            return self._commands.get(name)
        lowered = text.lower()
        matched_name = self._phrase_index.get(lowered)
        if matched_name is not None:
            return self._commands[matched_name]
        # Bare command name (no slash) is also a valid exact match target.
        return self._commands.get(lowered)

    def commands(self) -> list[Command]:
        return list(self._commands.values())
