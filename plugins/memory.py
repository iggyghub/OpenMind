"""
Memory MCP plugin — Issue #79, ADR-0005.

Tools:
  - memory_remember(fact) — store a fact in the active profile's long-term
    memory. Returns the memory id so the LLM can forget it later.
  - memory_recall(query, n_results?) — semantic search across the active
    profile's memory. Returns up to n_results facts ordered closest-first.
  - memory_forget(memory_id) — delete one memory by id.

Three structural firsts worth noting (locked in #79's sharpener):

  1. First plugin to declare ``REQUIRED_CAPABILITIES = frozenset()``. The
     plugin source contains no AST-completeness-tracked primitives (the
     ChromaDB / SQLite calls live inside MemoryManager, not here). The
     orchestrator validator at cerebral/mcp/orchestrator.py:50 explicitly
     names this as a supported case ("legitimately set REQUIRED_CAPABILITIES
     = frozenset()"). No runtime gate fires.

  2. First plugin whose state is per-profile. MemoryManager builds a
     per-profile Chroma collection ("profile_{id}"). The active profile can
     switch between calls, so the plugin re-resolves the factory at every
     tool call rather than caching at construction. Wired from main.py via
     ``set_memory_factory(_get_memory)``, mirroring the post-#50
     ``set_voice_prompt_fn`` pattern.

  3. No registration-time side effects — pure lazy construction. Constructing
     MemoryPlugin() does not connect to ChromaDB, does not query the active
     profile, does not touch disk. Opposite of plugins/homeassistant.py's
     deliberate registration-time urlopen ping.

Threat-model carve-out (acknowledged, deferred): ADR-0005 ranks
prompt-injection-to-tool-misuse as threat #1. A poisoned LLM that calls
memory_remember/forget can pollute or erase Felix's long-term beliefs. The
capability gate is the wrong mitigation here (per-write consent prompts kill
the UX); the right mitigation is a future Memory tray UI parallel to Insights
(list/edit/delete with the user in the loop). Out of scope for #79.
"""
from __future__ import annotations

import json
import logging
from typing import Callable, Optional

from cerebral.action_queue.manager import KIND_MEMORY_PROPOSAL, QueueManager
from cerebral.mcp.orchestrator import Tool, ToolResult
from cerebral.memory.manager import MemoryManager

logger = logging.getLogger(__name__)

PLUGIN_NAME = "memory"

# Empty: see module docstring. Plugin source contains no AST-tracked
# primitives; all storage calls live inside MemoryManager. Validator path is
# the registration happy path (cerebral/mcp/orchestrator.py:119-141).
REQUIRED_CAPABILITIES: frozenset[str] = frozenset()

# Canonical error vocabulary (six strings, mirrors plugins/homeassistant.py
# pattern). Generic catch-all is the seventh.
_NO_ACTIVE_PROFILE_MSG = "Memory is not available — no active profile"
_FACTORY_NOT_WIRED_MSG = "Memory is not available — factory not wired"
_BLANK_FACT_MSG = "'fact' is required for memory_remember"
_BLANK_QUERY_MSG = "'query' is required for memory_recall"
_BLANK_ID_MSG = "'memory_id' is required for memory_forget"
_NOT_FOUND_TEMPLATE = "Memory not found: '{}'"
_GENERIC_ERROR_MSG = "Memory operation failed"

_DEFAULT_N_RESULTS = 5
_MAX_N_RESULTS = 20

MemoryFactory = Callable[[], Optional[MemoryManager]]
QueueFactory = Callable[[], Optional[QueueManager]]

# Module-level factories set by cerebral/main.py post-orchestrator-boot.
# Tests bypass by passing directly to MemoryPlugin's constructor.
_memory_factory: Optional[MemoryFactory] = None
_queue_factory: Optional[QueueFactory] = None


def set_memory_factory(fn: MemoryFactory) -> None:
    """Wire main.py's _get_memory() — called once at startup after the
    orchestrator has discovered the plugin.

    The factory must return ``MemoryManager | None``: ``None`` when no
    profile is loaded, a fresh ``MemoryManager`` for the active profile
    otherwise. (The active profile can change between calls; the factory
    re-resolves each time.)
    """
    global _memory_factory
    _memory_factory = fn


def set_queue_factory(fn: QueueFactory) -> None:
    global _queue_factory
    _queue_factory = fn


class MemoryPlugin:
    name = PLUGIN_NAME

    def __init__(
        self,
        memory_factory: Optional[MemoryFactory] = None,
        queue_factory: Optional[QueueFactory] = None,
    ) -> None:
        # Constructor-injected factories win over module-level setters.
        # Tests pass directly; production wires via main.py at startup.
        self._factory = memory_factory
        self._queue_factory = queue_factory

    def list_tools(self) -> list[Tool]:
        return [
            Tool(
                name="propose_memory",
                description=(
                    "Propose storing a durable fact about the user in long-term "
                    "memory. The user must confirm before the fact is saved. Use "
                    "this when the user states a preference, relationship, important "
                    "date, or ongoing context about themselves. Never call "
                    "memory_remember directly -- always propose first."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "fact": {
                            "type": "string",
                            "description": "The fact to propose remembering. One coherent statement.",
                        },
                    },
                    "required": ["fact"],
                },
            ),
            Tool(
                name="memory_remember",
                description=(
                    "Store a fact in your long-term memory for the active "
                    "profile. Use for things the user wants you to remember: "
                    "preferences, relationships, important dates, ongoing "
                    "context. Returns the memory id so you can forget it "
                    "later if needed."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "fact": {
                            "type": "string",
                            "description": "The fact to remember. One coherent statement.",
                        },
                    },
                    "required": ["fact"],
                },
            ),
            Tool(
                name="memory_recall",
                description=(
                    "Search your long-term memory for facts relevant to a "
                    "query. Returns up to n_results most semantically similar "
                    "facts, ordered closest-first. Empty list when nothing "
                    "matches or memory is empty."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "What to search for. Free-form natural language.",
                        },
                        "n_results": {
                            "type": "integer",
                            "description": "Max number of results (default 5, capped at 20).",
                            "default": _DEFAULT_N_RESULTS,
                        },
                    },
                    "required": ["query"],
                },
            ),
            Tool(
                name="memory_forget",
                description=(
                    "Delete a specific memory by id. Use the id returned by "
                    "memory_remember, or the id field from a memory_recall "
                    "result. Returns confirmation when successful."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "memory_id": {
                            "type": "string",
                            "description": "The memory id (uuid) to delete.",
                        },
                    },
                    "required": ["memory_id"],
                },
            ),
        ]

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        if tool_name == "propose_memory":
            return self._propose(args)
        if tool_name == "memory_remember":
            return await self._remember(args)
        if tool_name == "memory_recall":
            return await self._recall(args)
        if tool_name == "memory_forget":
            return await self._forget(args)
        return ToolResult(content=f"Unknown tool: '{tool_name}'", is_error=True)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _resolve_queue(self) -> tuple[Optional[QueueManager], Optional[str]]:
        factory = self._queue_factory or _queue_factory
        if factory is None:
            return None, "Queue not available -- factory not wired"
        queue = factory()
        if queue is None:
            return None, "Queue not available"
        return queue, None

    def _propose(self, args: dict) -> ToolResult:
        fact = args.get("fact", "")
        if not isinstance(fact, str) or not fact.strip():
            return ToolResult(content=_BLANK_FACT_MSG, is_error=True)
        fact = fact.strip()
        queue, err = self._resolve_queue()
        if err is not None:
            return ToolResult(content=err, is_error=True)
        queue.add_item(
            f"Remember: {fact}",
            fact,
            kind=KIND_MEMORY_PROPOSAL,
            tool_args={"fact": fact},
        )
        return ToolResult(content="Memory proposal added -- waiting for your confirmation")

    def _resolve_memory(self) -> tuple[Optional[MemoryManager], Optional[str]]:
        """Return ``(manager, error_string)``. Exactly one is non-None.

        ``error_string`` is one of the canonical messages so callers can
        return ``ToolResult(content=err, is_error=True)`` directly.
        """
        factory = self._factory or _memory_factory
        if factory is None:
            return None, _FACTORY_NOT_WIRED_MSG
        manager = factory()
        if manager is None:
            return None, _NO_ACTIVE_PROFILE_MSG
        return manager, None

    @staticmethod
    def _clamp_n_results(raw: object) -> int:
        try:
            n = int(raw)
        except (TypeError, ValueError):
            return _DEFAULT_N_RESULTS
        if n < 1:
            return _DEFAULT_N_RESULTS
        if n > _MAX_N_RESULTS:
            return _MAX_N_RESULTS
        return n

    async def _remember(self, args: dict) -> ToolResult:
        fact = args.get("fact", "")
        if not isinstance(fact, str) or not fact.strip():
            return ToolResult(content=_BLANK_FACT_MSG, is_error=True)
        manager, err = self._resolve_memory()
        if err is not None:
            return ToolResult(content=err, is_error=True)
        try:
            memory_id = await manager.remember(fact)
        except Exception:
            logger.warning("[memory] memory_remember failed", exc_info=True)
            return ToolResult(content=_GENERIC_ERROR_MSG, is_error=True)
        return ToolResult(content=json.dumps({"memory_id": memory_id}))

    async def _recall(self, args: dict) -> ToolResult:
        query = args.get("query", "")
        if not isinstance(query, str) or not query.strip():
            return ToolResult(content=_BLANK_QUERY_MSG, is_error=True)
        n_results = self._clamp_n_results(args.get("n_results", _DEFAULT_N_RESULTS))
        manager, err = self._resolve_memory()
        if err is not None:
            return ToolResult(content=err, is_error=True)
        try:
            memories = await manager.recall(query, n_results=n_results)
        except Exception:
            logger.warning("[memory] memory_recall failed", exc_info=True)
            return ToolResult(content=_GENERIC_ERROR_MSG, is_error=True)
        payload = {
            "memories": [
                {"id": m.id, "fact": m.fact, "distance": m.distance}
                for m in memories
            ]
        }
        return ToolResult(content=json.dumps(payload))

    async def _forget(self, args: dict) -> ToolResult:
        memory_id = args.get("memory_id", "")
        if not isinstance(memory_id, str) or not memory_id.strip():
            return ToolResult(content=_BLANK_ID_MSG, is_error=True)
        manager, err = self._resolve_memory()
        if err is not None:
            return ToolResult(content=err, is_error=True)
        try:
            ok = await manager.forget(memory_id)
        except Exception:
            logger.warning("[memory] memory_forget failed", exc_info=True)
            return ToolResult(content=_GENERIC_ERROR_MSG, is_error=True)
        if not ok:
            return ToolResult(
                content=_NOT_FOUND_TEMPLATE.format(memory_id),
                is_error=True,
            )
        return ToolResult(content=json.dumps({"forgotten": True}))


def create() -> MemoryPlugin:
    return MemoryPlugin()
