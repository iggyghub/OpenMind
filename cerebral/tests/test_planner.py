"""
Planner unit tests -- Issue #274 (S1 single-step engine).

All tests use an injected fake backend (no HTTP).
Integration tests (@pytest.mark.integration) hit real services and are skipped
unless explicitly selected.
"""
import pytest
from unittest.mock import AsyncMock

from cerebral.llm.planner import Planner, validate_tool_args
from cerebral.llm.router import ToolCall


_CLOCK_TOOL = {
    "name": "get_time",
    "description": "Get the current time in a timezone",
    "input_schema": {
        "type": "object",
        "properties": {"timezone": {"type": "string"}},
        "required": ["timezone"],
    },
}

_PING_TOOL = {
    "name": "ping",
    "description": "Ping a host",
    "input_schema": {
        "type": "object",
        "properties": {
            "host": {"type": "string"},
            "count": {"type": "integer"},
            "verbose": {"type": "boolean"},
        },
        "required": ["host"],
    },
}

_FAKE_TOOLS = [_CLOCK_TOOL, _PING_TOOL]


# ---------------------------------------------------------------------------
# Planner.plan() -- ToolCall path
# ---------------------------------------------------------------------------

async def test_planner_returns_tool_call_when_backend_does():
    backend = AsyncMock()
    backend.complete_with_tools.return_value = ToolCall(name="get_time", args={"timezone": "Asia/Tokyo"})
    result = await Planner(backend).plan("What time is it in Tokyo?", _FAKE_TOOLS)
    assert isinstance(result, ToolCall)
    assert result.name == "get_time"
    assert result.args == {"timezone": "Asia/Tokyo"}


async def test_planner_passes_tools_to_backend():
    backend = AsyncMock()
    backend.complete_with_tools.return_value = "ok"
    await Planner(backend).plan("hi", _FAKE_TOOLS)
    _, call_tools = backend.complete_with_tools.call_args[0]
    assert call_tools == _FAKE_TOOLS


# ---------------------------------------------------------------------------
# Planner.plan() -- text path
# ---------------------------------------------------------------------------

async def test_planner_returns_str_when_backend_does():
    backend = AsyncMock()
    backend.complete_with_tools.return_value = "Jazz is a beautiful musical tradition."
    result = await Planner(backend).plan("What do you think of jazz?", _FAKE_TOOLS)
    assert isinstance(result, str)
    assert "Jazz" in result


# ---------------------------------------------------------------------------
# Planner.plan() -- system prompt
# ---------------------------------------------------------------------------

async def test_planner_includes_system_prompt_in_call():
    backend = AsyncMock()
    backend.complete_with_tools.return_value = "ok"
    await Planner(backend).plan("hi", _FAKE_TOOLS)
    prompt, _ = backend.complete_with_tools.call_args[0]
    assert "felix" in prompt.lower() or "assistant" in prompt.lower()


async def test_planner_embeds_transcript_in_prompt():
    backend = AsyncMock()
    backend.complete_with_tools.return_value = "ok"
    await Planner(backend).plan("ping google.com please", _FAKE_TOOLS)
    prompt, _ = backend.complete_with_tools.call_args[0]
    assert "ping google.com please" in prompt


# ---------------------------------------------------------------------------
# Planner.plan() -- re-ask (error= path)
# ---------------------------------------------------------------------------

async def test_planner_with_error_embeds_error_in_prompt():
    backend = AsyncMock()
    backend.complete_with_tools.return_value = ToolCall(name="get_time", args={"timezone": "UTC"})
    await Planner(backend).plan("hi", _FAKE_TOOLS, error="Missing required argument 'timezone'")
    prompt, _ = backend.complete_with_tools.call_args[0]
    assert "Missing required argument" in prompt


async def test_planner_without_error_excludes_error_boilerplate():
    backend = AsyncMock()
    backend.complete_with_tools.return_value = "ok"
    await Planner(backend).plan("hi", _FAKE_TOOLS)
    prompt, _ = backend.complete_with_tools.call_args[0]
    assert "previous tool call failed" not in prompt


# ---------------------------------------------------------------------------
# validate_tool_args -- passing cases
# ---------------------------------------------------------------------------

def test_validate_passes_all_required_present():
    assert validate_tool_args("get_time", {"timezone": "UTC"}, _FAKE_TOOLS) is None


def test_validate_passes_extra_args_allowed():
    assert validate_tool_args("get_time", {"timezone": "UTC", "extra": "x"}, _FAKE_TOOLS) is None


def test_validate_passes_for_unknown_tool():
    assert validate_tool_args("nonexistent", {}, _FAKE_TOOLS) is None


def test_validate_passes_no_required_field():
    tools = [{"name": "noop", "description": "", "input_schema": {"type": "object", "properties": {}}}]
    assert validate_tool_args("noop", {}, tools) is None


def test_validate_passes_correct_types():
    assert validate_tool_args("ping", {"host": "google.com", "count": 3, "verbose": True}, _FAKE_TOOLS) is None


# ---------------------------------------------------------------------------
# validate_tool_args -- failing cases
# ---------------------------------------------------------------------------

def test_validate_fails_missing_required_field():
    err = validate_tool_args("get_time", {}, _FAKE_TOOLS)
    assert err is not None
    assert "timezone" in err


def test_validate_fails_missing_one_of_multiple_required():
    err = validate_tool_args("ping", {}, _FAKE_TOOLS)
    assert err is not None
    assert "host" in err


def test_validate_fails_wrong_type_string():
    err = validate_tool_args("get_time", {"timezone": 123}, _FAKE_TOOLS)
    assert err is not None
    assert "string" in err


def test_validate_fails_wrong_type_integer():
    err = validate_tool_args("ping", {"host": "x.com", "count": "three"}, _FAKE_TOOLS)
    assert err is not None
    assert "integer" in err


def test_validate_fails_wrong_type_boolean():
    err = validate_tool_args("ping", {"host": "x.com", "verbose": "yes"}, _FAKE_TOOLS)
    assert err is not None
    assert "boolean" in err


# ---------------------------------------------------------------------------
# Integration smoke tests (skipped without real services)
# ---------------------------------------------------------------------------

@pytest.mark.integration
async def test_ollama_complete_with_tools_smoke():
    """OllamaBackend /api/chat with a tool-capable model returns ToolCall or str."""
    import httpx
    from cerebral.llm.router import OllamaBackend

    try:
        tags = httpx.get("http://localhost:11434/api/tags", timeout=2.0).json()
        models = [m["name"] for m in (tags.get("models") or [])]
    except Exception:
        pytest.skip("Ollama not reachable")

    tool_model = next((m for m in models if "qwen" in m.lower()), None)
    if tool_model is None:
        pytest.skip("No tool-capable (qwen) model installed")

    backend = OllamaBackend(url="http://localhost:11434", model=tool_model)
    result = await backend.complete_with_tools(
        "You are Felix, a personal AI assistant with access to tools. "
        "When a user request can be fulfilled by one of your tools, use it.\n\n"
        "User: What time is it in Tokyo?",
        [_CLOCK_TOOL],
    )
    assert isinstance(result, (ToolCall, str))
    if isinstance(result, ToolCall):
        assert result.name == "get_time"


@pytest.mark.integration
async def test_claw_complete_with_tools_fail_soft():
    """ClawBackend returns ToolCall or fails-soft to str when tool_calls absent."""
    from cerebral.llm.router import ClawBackend

    backend = ClawBackend(url="http://localhost:3000")
    try:
        result = await backend.complete_with_tools(
            "You are Felix, a personal AI assistant with access to tools. "
            "When a user request can be fulfilled by one of your tools, use it.\n\n"
            "User: What time is it in Tokyo?",
            [_CLOCK_TOOL],
        )
    except ConnectionError:
        pytest.skip("OpenClaw not reachable")

    assert isinstance(result, (ToolCall, str))


# ── shortlist_tools — context-window tool pre-selection ───────────────────────

def _fake_registry(n: int = 200) -> list[dict]:
    tools = [
        {
            "name": f"filler_tool_{i}",
            "description": f"Filler capability number {i} for padding the registry",
            "input_schema": {"type": "object", "properties": {}},
        }
        for i in range(n)
    ]
    tools.append({
        "name": "jobs_store_resume",
        "description": (
            "Store the user's resume PDF as their Resume artifact and parse it "
            "into a structured Applicant dossier"
        ),
        "input_schema": {"type": "object", "properties": {}},
    })
    return tools


def test_shortlist_ranks_matching_tool_into_subset():
    from cerebral.llm.planner import shortlist_tools

    subset = shortlist_tools("store this as my resume", _fake_registry(), limit=30)
    assert len(subset) == 30
    assert any(t["name"] == "jobs_store_resume" for t in subset)


def test_shortlist_small_registry_passes_through():
    from cerebral.llm.planner import shortlist_tools

    tools = _fake_registry(10)
    assert shortlist_tools("store this as my resume", tools, limit=30) == tools


def test_shortlist_no_usable_words_passes_through():
    from cerebral.llm.planner import shortlist_tools

    tools = _fake_registry()
    # every word under 4 chars -> no signal -> don't guess, send everything
    assert shortlist_tools("hi do it now", tools, limit=30) == tools


# ── ADR-0016 S7 (#580) — stealth-vs-fast web routing heuristic ────────────────

_BROWSER_NAVIGATE_TOOL = {
    "name": "browser_navigate",
    "description": "OS-level browser drive (computer_use, stealth path)",
    "input_schema": {"type": "object", "properties": {}},
}

_BROWSER_PLUGIN_NAVIGATE = {
    "name": "navigate",
    "description": "Playwright DOM navigate (Browser plugin, fast path)",
    "input_schema": {"type": "object", "properties": {}},
}

_UNRELATED_TOOL = {
    "name": "get_time",
    "description": "Get the current time",
    "input_schema": {"type": "object", "properties": {}},
}


def test_prefer_web_path_stealth_url_prefers_computer_use():
    from cerebral.llm.planner import prefer_web_path

    tools = [_BROWSER_PLUGIN_NAVIGATE, _UNRELATED_TOOL, _BROWSER_NAVIGATE_TOOL]
    out = prefer_web_path("open https://discord.com/app for me", tools)
    # computer_use browser_navigate should sit first; Browser plugin's navigate
    # should sit last; the unrelated tool preserves its middle position.
    assert out[0]["name"] == "browser_navigate"
    assert out[-1]["name"] == "navigate"


def test_prefer_web_path_benign_url_prefers_browser_plugin():
    from cerebral.llm.planner import prefer_web_path

    tools = [_BROWSER_NAVIGATE_TOOL, _UNRELATED_TOOL, _BROWSER_PLUGIN_NAVIGATE]
    out = prefer_web_path("please read https://example.com/article", tools)
    assert out[0]["name"] == "navigate"
    assert out[-1]["name"] == "browser_navigate"


def test_prefer_web_path_no_url_is_noop():
    from cerebral.llm.planner import prefer_web_path

    tools = [_BROWSER_NAVIGATE_TOOL, _BROWSER_PLUGIN_NAVIGATE, _UNRELATED_TOOL]
    out = prefer_web_path("what time is it in Tokyo?", tools)
    assert [t["name"] for t in out] == [
        "browser_navigate", "navigate", "get_time",
    ]


def test_shortlist_biases_stealth_url_toward_computer_use():
    """Even in a small registry that passes the shortlist word-count guard,
    a stealth URL still promotes the computer_use tool to the front."""
    from cerebral.llm.planner import shortlist_tools

    tools = [_BROWSER_PLUGIN_NAVIGATE, _UNRELATED_TOOL, _BROWSER_NAVIGATE_TOOL]
    out = shortlist_tools("please open https://discord.com/app", tools, limit=30)
    assert out[0]["name"] == "browser_navigate"


def test_shortlist_biases_benign_url_toward_browser_plugin():
    from cerebral.llm.planner import shortlist_tools

    tools = [_BROWSER_NAVIGATE_TOOL, _UNRELATED_TOOL, _BROWSER_PLUGIN_NAVIGATE]
    out = shortlist_tools("read https://example.com/post please", tools, limit=30)
    assert out[0]["name"] == "navigate"


def test_coexistence_browser_plugin_still_registers_three_tools():
    """ADR-0016 sec 2 coexistence: the Browser plugin (Playwright) keeps its
    role for benign/anonymous use -- computer_use does not replace it."""
    from plugins.browser import BrowserPlugin
    names = {t.name for t in BrowserPlugin().list_tools()}
    assert names == {"web_search", "navigate", "read_pdf"}
