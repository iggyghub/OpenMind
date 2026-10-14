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
import subprocess
import sys
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
_REPO_ROOT = Path(__file__).resolve().parent.parent

# A build declares which spec facts it implements in a manifest committed with
# the built code: plugins/rebuild_manifests/<name>.json
#   {"implements": {"F-ipc-channel-1": {"symbol": "plugins/x.py::tool", "test":
#    "cerebral/tests/test_x.py::test_case"}}, "waived": {"F-storage-2": "reason"}}
_MANIFEST_DIR = "plugins/rebuild_manifests"
_TEST_ID_RE = re.compile(r"^cerebral/tests/[A-Za-z0-9_./-]+\.py::[A-Za-z0-9_\[\]-]+$")
_TEST_TIMEOUT_S = 180
_MAX_TESTS = 60

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

    def __init__(
        self,
        run_dir: Path | None = None,
        rebuild_dir: Path | None = None,
        repo_root: Path | None = None,
        run_fn=None,
    ) -> None:
        self._run_dir = Path(run_dir) if run_dir else _DEFAULT_RUN_DIR
        self._rebuild_dir = Path(rebuild_dir) if rebuild_dir else _DEFAULT_REBUILD_DIR
        self._repo_root = Path(repo_root) if repo_root else _REPO_ROOT
        self._run_fn = run_fn or subprocess.run

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
            Tool(
                name="rebuild_score",
                description=(
                    "Score a rebuild: for each fact in its spec, is it implemented and tested? "
                    "Reads the build's manifest (plugins/rebuild_manifests/<name>.json), runs the "
                    "referenced tests, writes scorecard.json and returns the coverage and the "
                    "unmet facts. Static coverage of the evidence only, not behavioural equivalence."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "The rebuild's name."},
                    },
                    "required": ["name"],
                },
            ),
        ]

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        args = args or {}
        if tool_name == "rebuild_new":
            return self._new(args.get("name"), args.get("run_id"), args.get("feature"))
        if tool_name == "rebuild_score":
            return self._score(args.get("name"))
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



    # -- scoring (RB2) ---------------------------------------------------

    def _load_manifest(self, name: str) -> dict:
        path = self._repo_root / _MANIFEST_DIR / f"{name}.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _check_entry(self, entry: dict, budget: list) -> tuple[str, str]:
        """('met'|'unmet', why) for one manifest 'implements' entry."""
        symbol, test = entry.get("symbol"), entry.get("test")
        if not isinstance(symbol, str) or not symbol.strip():
            return "unmet", "manifest entry has no symbol"
        if not isinstance(test, str) or not _TEST_ID_RE.match(test):
            return "unmet", "manifest entry has no valid test id"
        test_file = (self._repo_root / test.split("::")[0]).resolve()
        try:
            test_file.relative_to(self._repo_root.resolve())
        except ValueError:
            return "unmet", "test path is outside the repository"
        if not test_file.is_file():
            return "unmet", "referenced test file does not exist"
        if budget[0] <= 0:
            return "unmet", "test budget exceeded; score again"
        budget[0] -= 1
        try:
            proc = self._run_fn(
                [sys.executable, "-m", "pytest", "-q", "-x", test],
                cwd=str(self._repo_root), capture_output=True, text=True,
                timeout=_TEST_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired:
            return "unmet", "referenced test timed out"
        except Exception as exc:
            return "unmet", f"referenced test could not run: {_clip(exc)}"
        if proc.returncode != 0:
            return "unmet", "referenced test failed or was not found"
        return "met", "implemented and its test passes"

    def _score(self, name) -> ToolResult:
        if not isinstance(name, str) or not _NAME_RE.match(name):
            return ToolResult(
                content="'name' must be 1-40 chars of lowercase letters, digits and dashes",
                is_error=True,
            )
        spec_path = self._rebuild_dir / name / "spec.json"
        try:
            spec = json.loads(spec_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return ToolResult(content=f"no spec for rebuild '{name}'", is_error=True)
        manifest = self._load_manifest(name)
        implements = manifest.get("implements") if isinstance(manifest.get("implements"), dict) else {}
        waived = manifest.get("waived") if isinstance(manifest.get("waived"), dict) else {}

        budget = [_MAX_TESTS]
        results = []
        for fact in spec.get("facts") or []:
            fid = fact.get("id")
            entry = implements.get(fid)
            reason = waived.get(fid)
            if isinstance(entry, dict):
                status, why = self._check_entry(entry, budget)
            elif isinstance(reason, str) and reason.strip():
                status, why = "waived", _clip(reason)
            elif fact.get("confidence") != "exact":
                status, why = "unknown", "REA was not certain about this fact"
            else:
                status, why = "unmet", "not in the manifest"
            results.append({
                "id": fid, "kind": fact.get("kind"), "label": _clip(fact.get("label")),
                "status": status, "why": why,
            })

        counts = {"met": 0, "unmet": 0, "unknown": 0, "waived": 0}
        for r in results:
            counts[r["status"]] += 1
        scorable = counts["met"] + counts["unmet"]
        card = {
            "version": 1,
            "name": name,
            "scored_at": datetime.now(timezone.utc).isoformat(),
            "counts": counts,
            "total": len(results),
            "percent_met": round(100.0 * counts["met"] / scorable, 1) if scorable else None,
            "facts": results,
            "note": (
                "Static coverage of REA's observed facts only. 'met' means a manifest entry "
                "exists and its test passes; it does not show the copy behaves the same."
            ),
        }
        (self._rebuild_dir / name).mkdir(parents=True, exist_ok=True)
        (self._rebuild_dir / name / "scorecard.json").write_text(json.dumps(card, indent=2), encoding="utf-8")
        return ToolResult(content=json.dumps({
            "name": name,
            "counts": counts,
            "total": card["total"],
            "percent_met": card["percent_met"],
            "unmet": [r for r in results if r["status"] == "unmet"][:20],
            "scorecard_path": str(self._rebuild_dir / name / "scorecard.json"),
        }))


def create(**kwargs) -> RebuildPlugin:
    return RebuildPlugin()
