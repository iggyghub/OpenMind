"""
Rebuild plugin (ADR-0040): turn a saved REA run into a rebuild spec.

A rebuild lives in cerebral/data/rebuilds/<name>/. This slice (RB1) creates
spec.json from the *compact* application graph REA already returns
(normalized_result.graph: windows, IPC channels and handlers, bridge APIs,
endpoints, storage), not from the multi-megabyte semantic graph. Every fact
gets a stable id (F-<kind>-<n>) so a later scorer can check it off.

Evidence is untrusted input (ADR-0040 decision 7): every string taken from the
analysed app is length-capped and stored as structured data, never as
instructions.

Tool: rebuild_new.
"""
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from cerebral.mcp.orchestrator import Tool, ToolResult

PLUGIN_NAME = "rebuild"

# ADR-0005: reads a saved REA run (fs_read) and writes the spec (fs_write).
# The 16-class vocabulary is unchanged.
REQUIRED_CAPABILITIES: frozenset[str] = frozenset({"fs_read", "fs_write"})

_DATA = Path(__file__).resolve().parent.parent / "cerebral" / "data"
_DEFAULT_RUN_DIR = _DATA / "rea_runs"
_DEFAULT_REBUILD_DIR = _DATA / "rebuilds"

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
_RUN_RE = re.compile(r"^[0-9a-f]{12}$")
_MAX_TEXT = 200
_MAX_LIST = 50

# REA graph node kind -> spec fact kind.
FACT_KINDS = {
    "browser-window": "window",
    "ipc-channel": "ipc-channel",
    "ipc-handler": "ipc-handler",
    "context-bridge-api": "bridge-api",
    "endpoint": "endpoint",
    "storage": "storage",
}


def _clip(value) -> str:
    return str(value if value is not None else "")[:_MAX_TEXT]


def _source(first_obs: dict):
    ev = first_obs.get("evidence") or {}
    loc = (ev.get("location") or {}).get("value") or {}
    src = loc.get("source")
    if not src:
        return None
    line = (loc.get("start") or {}).get("line")
    return _clip(f"{src}:{line}" if line is not None else src)


def _detail(kind: str, observations: list) -> dict:
    props = [(o.get("properties") or {}) for o in observations]
    first = props[0] if props else {}
    if kind == "window":
        prefs = {}
        for p in (first.get("web_preferences") or [])[:_MAX_LIST]:
            val = (p.get("value") or {}).get("value")
            prefs[_clip(p.get("name"))] = val if isinstance(val, (bool, int, float)) else _clip(val)
        return {"web_preferences": prefs, "preload_path": _clip(first.get("preload_path")) or None}
    if kind == "ipc-channel":
        return {
            "sides": sorted({_clip(p.get("side")) for p in props if p.get("side")}),
            "operations": sorted({_clip(p.get("operation")) for p in props if p.get("operation")}),
        }
    if kind == "ipc-handler":
        return {"handler_kind": _clip(first.get("handler_kind")) or None}
    if kind == "bridge-api":
        members = [_clip(m) for m in (first.get("members") or [])[:_MAX_LIST]]
        return {"members": members, "unknown_members": int(first.get("unknown_members") or 0)}
    if kind == "endpoint":
        return {"endpoint_kind": _clip(first.get("endpoint_kind")) or None,
                "value": _clip(first.get("value")) or None}
    if kind == "storage":
        return {"storage_kind": _clip(first.get("storage_kind")) or None,
                "name": _clip(first.get("name")) or None}
    return {}


def facts_from_graph(graph: dict) -> list[dict]:
    """Deterministic fact list from REA's compact application graph."""
    raw = []
    for node in (graph or {}).get("nodes") or []:
        kind = FACT_KINDS.get(node.get("kind"))
        if not kind:
            continue
        observations = node.get("observations") or []
        first = observations[0] if observations else {}
        raw.append({
            "kind": kind,
            "label": _clip(first.get("label")),
            "node_id": _clip(node.get("node_id")),
            "confidence": _clip((first.get("evidence") or {}).get("confidence")) or None,
            "source": _source(first),
            "detail": _detail(kind, observations),
        })
    raw.sort(key=lambda f: (f["kind"], f["label"], f["node_id"]))
    counters: dict[str, int] = {}
    for fact in raw:
        counters[fact["kind"]] = counters.get(fact["kind"], 0) + 1
        fact["id"] = f"F-{fact['kind']}-{counters[fact['kind']]}"
    return raw


class RebuildPlugin:
    name = PLUGIN_NAME

    def __init__(self, run_dir: Path | None = None, rebuild_dir: Path | None = None) -> None:
        self._run_dir = Path(run_dir) if run_dir else _DEFAULT_RUN_DIR
        self._rebuild_dir = Path(rebuild_dir) if rebuild_dir else _DEFAULT_REBUILD_DIR

    def list_tools(self) -> list[Tool]:
        return [
            Tool(
                name="rebuild_new",
                description=(
                    "Start a rebuild: create a spec (a numbered checklist of facts: windows, "
                    "IPC channels, bridge APIs, endpoints, storage) from a saved REA analysis "
                    "run. Returns the fact counts and where the spec was written."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Short id for this rebuild: lowercase letters, digits, dashes.",
                        },
                        "run_id": {
                            "type": "string",
                            "description": "run_id of a saved REA analysis (12 hex characters).",
                        },
                        "feature": {
                            "type": "string",
                            "description": "Optional one-line name of the feature being rebuilt.",
                        },
                    },
                    "required": ["name", "run_id"],
                },
            ),
        ]

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        args = args or {}
        if tool_name == "rebuild_new":
            return self._new(args.get("name"), args.get("run_id"), args.get("feature"))
        return ToolResult(content=f"Unknown tool: '{tool_name}'", is_error=True)

    def _new(self, name, run_id, feature) -> ToolResult:
        if not isinstance(name, str) or not _NAME_RE.match(name):
            return ToolResult(
                content="'name' must be 1-40 chars of lowercase letters, digits and dashes",
                is_error=True,
            )
        if not isinstance(run_id, str) or not _RUN_RE.match(run_id):
            return ToolResult(content="'run_id' must be 12 hex characters", is_error=True)
        run_file = self._run_dir / f"{run_id}.json"
        if not run_file.is_file():
            return ToolResult(content=f"no saved REA run '{run_id}'", is_error=True)
        try:
            doc = json.loads(run_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return ToolResult(content="saved REA run is unreadable", is_error=True)
        graph = ((doc.get("normalized_result") or {}).get("graph")) if isinstance(doc, dict) else None
        if not isinstance(graph, dict):
            return ToolResult(content="this run has no application graph to build a spec from", is_error=True)

        facts = facts_from_graph(graph)
        counts: dict[str, int] = {}
        for fact in facts:
            counts[fact["kind"]] = counts.get(fact["kind"], 0) + 1
        limitations = [_clip(x) for x in (doc.get("limitations") or [])[:20]]
        spec = {
            "version": 1,
            "name": name,
            "feature": _clip(feature) or None,
            "created": datetime.now(timezone.utc).isoformat(),
            "source": {
                "run_id": run_id,
                "target": _clip((doc.get("subject") or {}).get("name")),
                "confidence": _clip(doc.get("confidence")) or None,
            },
            "counts": counts,
            "facts": facts,
            "limitations": limitations,
            "note": "Static evidence only (ADR-0040). Facts are what REA observed; absence is not proof.",
        }
        target = self._rebuild_dir / name
        target.mkdir(parents=True, exist_ok=True)
        spec_path = target / "spec.json"
        spec_path.write_text(json.dumps(spec, indent=2), encoding="utf-8")
        return ToolResult(content=json.dumps({
            "name": name,
            "spec_path": str(spec_path),
            "counts": counts,
            "fact_total": len(facts),
        }))


def create(**kwargs) -> RebuildPlugin:
    return RebuildPlugin()
