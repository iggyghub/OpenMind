"""
Environmental context tests — Issue #14.

Unit tests inject fake http_get / capture_fn / infer_fn; no network, no camera.

Slices:
  1. get_context() default state — all empty/false, required keys present
  2. refresh_location() populates city/country/lat/lon from injected response
  3. refresh_location() handles HTTP errors gracefully
  4. get_context() returns populated data after refresh_location()
  5. enable_camera() / disable_camera() toggle camera_enabled
  6. Camera capture loop stores scene result in get_context()
  7. Capture loop passes frame from capture_fn to infer_fn
  8. enable_camera() stores interval_seconds correctly
  9. FiveW1HExtractor.extract() — CandidateAction.context defaults to {}
 10. extract() without env_context → context stays {}
 11. extract() with env_context → context attached to returned action
 12. env_context=None → context stays {} (not passed through)
"""
import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock

from cerebral.environment.context import EnvironmentContext
from cerebral.passive.extractor import CandidateAction, FiveW1HExtractor
from cerebral.llm.router import ModelRouter


# ── helpers ────────────────────────────────────────────────────────────────────

def _make_http_get(payload: dict):
    """Return a sync callable that returns the given payload dict (or raises on None)."""
    if payload is None:
        def _raise(*_):
            raise OSError("network error")
        return _raise
    def _ok(*_):
        return payload
    return _ok


def _router(response: str) -> ModelRouter:
    backend = AsyncMock()
    backend.complete.return_value = response
    return ModelRouter(backends={"ollama/gemma4": backend}, default_model="ollama/gemma4")


def _valid_llm_response(**overrides) -> str:
    payload = {
        "title": "Call the dentist",
        "summary": "Felix detected: dentist appointment needs rescheduling",
        "fields": {
            "who": "dentist", "what": "call", "when": "today",
            "where": "", "why": "reschedule", "how": "phone",
        },
        "confidence": 0.85,
    }
    payload.update(overrides)
    return json.dumps(payload)


GEOLOCATION_RESPONSE = {
    "status": "success",
    "city": "London",
    "country": "United Kingdom",
    "lat": 51.5074,
    "lon": -0.1278,
}


# ── Slice 1: default state ────────────────────────────────────────────────────

def test_get_context_returns_dict():
    ctx = EnvironmentContext()
    result = ctx.get_context()
    assert isinstance(result, dict)


def test_get_context_has_required_keys():
    ctx = EnvironmentContext()
    result = ctx.get_context()
    for key in ("city", "country", "lat", "lon", "scene", "camera_enabled"):
        assert key in result, f"missing key: {key}"


def test_get_context_default_city_is_empty():
    ctx = EnvironmentContext()
    assert ctx.get_context()["city"] == ""


def test_get_context_default_camera_enabled_is_false():
    ctx = EnvironmentContext()
    assert ctx.get_context()["camera_enabled"] is False


def test_get_context_default_scene_is_empty():
    ctx = EnvironmentContext()
    assert ctx.get_context()["scene"] == ""


# ── Slice 2: refresh_location() populates data ────────────────────────────────

async def test_refresh_location_returns_dict():
    ctx = EnvironmentContext(http_get=_make_http_get(GEOLOCATION_RESPONSE))
    result = await ctx.refresh_location()
    assert isinstance(result, dict)


async def test_refresh_location_stores_city():
    ctx = EnvironmentContext(http_get=_make_http_get(GEOLOCATION_RESPONSE))
    await ctx.refresh_location()
    assert ctx.get_context()["city"] == "London"


async def test_refresh_location_stores_country():
    ctx = EnvironmentContext(http_get=_make_http_get(GEOLOCATION_RESPONSE))
    await ctx.refresh_location()
    assert ctx.get_context()["country"] == "United Kingdom"


async def test_refresh_location_stores_lat_lon():
    ctx = EnvironmentContext(http_get=_make_http_get(GEOLOCATION_RESPONSE))
    await ctx.refresh_location()
    c = ctx.get_context()
    assert c["lat"] == pytest.approx(51.5074)
    assert c["lon"] == pytest.approx(-0.1278)


# ── Slice 3: refresh_location() handles errors gracefully ────────────────────

async def test_refresh_location_network_error_returns_empty_context():
    ctx = EnvironmentContext(http_get=_make_http_get(None))  # raises OSError
    result = await ctx.refresh_location()
    assert result["city"] == ""


async def test_refresh_location_network_error_does_not_raise():
    ctx = EnvironmentContext(http_get=_make_http_get(None))
    # must not raise — graceful degradation
    await ctx.refresh_location()


async def test_refresh_location_failed_status_returns_empty():
    ctx = EnvironmentContext(http_get=_make_http_get({"status": "fail"}))
    result = await ctx.refresh_location()
    assert result["city"] == ""


# ── Slice 4: get_context() returns populated data after refresh ───────────────

async def test_get_context_after_refresh_has_city():
    ctx = EnvironmentContext(http_get=_make_http_get(GEOLOCATION_RESPONSE))
    await ctx.refresh_location()
    assert ctx.get_context()["city"] == "London"


# ── Slice 5: enable_camera / disable_camera ───────────────────────────────────

def test_camera_enabled_property_starts_false():
    ctx = EnvironmentContext()
    assert ctx.camera_enabled is False


def test_enable_camera_sets_camera_enabled_true():
    ctx = EnvironmentContext(capture_fn=lambda: b"frame", infer_fn=lambda f: "office")
    ctx.enable_camera()
    assert ctx.camera_enabled is True
    ctx.disable_camera()  # cleanup — stop any background loop


def test_disable_camera_sets_camera_enabled_false():
    ctx = EnvironmentContext(capture_fn=lambda: b"frame", infer_fn=lambda f: "office")
    ctx.enable_camera()
    ctx.disable_camera()
    assert ctx.camera_enabled is False


def test_disable_camera_when_already_disabled_is_safe():
    ctx = EnvironmentContext()
    ctx.disable_camera()  # must not raise
    assert ctx.camera_enabled is False


# ── Slice 6: scene appears in get_context() after capture loop fires ──────────

async def test_capture_loop_result_appears_in_scene():
    captured_frames = []
    infer_calls = []

    def capture():
        return b"frame_data"

    def infer(frame):
        return "home office with two monitors"

    ctx = EnvironmentContext(capture_fn=capture, infer_fn=infer, interval_seconds=0.05)
    ctx.enable_camera()
    await asyncio.sleep(0.15)  # allow at least one capture cycle
    ctx.disable_camera()

    assert ctx.get_context()["scene"] == "home office with two monitors"


# ── Slice 7: capture loop passes frame to infer_fn ────────────────────────────

async def test_capture_loop_passes_frame_to_infer_fn():
    received = []

    def capture():
        return b"raw_frame"

    def infer(frame):
        received.append(frame)
        return "kitchen"

    ctx = EnvironmentContext(capture_fn=capture, infer_fn=infer, interval_seconds=0.05)
    ctx.enable_camera()
    await asyncio.sleep(0.15)
    ctx.disable_camera()

    assert b"raw_frame" in received


# ── Slice 8: enable_camera() stores interval ─────────────────────────────────

def test_enable_camera_with_custom_interval():
    ctx = EnvironmentContext(capture_fn=lambda: b"", infer_fn=lambda f: "")
    ctx.enable_camera(interval_seconds=60)
    assert ctx._interval_seconds == 60
    ctx.disable_camera()


# ── Slice 9: CandidateAction.context defaults to {} ──────────────────────────

def test_candidate_action_context_defaults_to_empty_dict():
    action = CandidateAction(title="Test", summary="s", fields={}, confidence=0.9)
    assert action.context == {}


# ── Slice 10: extract() without env_context → context stays {} ───────────────

async def test_extract_without_env_context_has_empty_context():
    ext = FiveW1HExtractor(_router(_valid_llm_response()))
    result = await ext.extract("I need to call the dentist")
    assert result is not None
    assert result.context == {}


# ── Slice 11: extract() with env_context → attached to CandidateAction ────────

async def test_extract_with_env_context_attaches_city():
    ext = FiveW1HExtractor(_router(_valid_llm_response()))
    env = {"city": "London", "country": "United Kingdom", "scene": "home office"}
    result = await ext.extract("I need to call the dentist", env_context=env)
    assert result is not None
    assert result.context["city"] == "London"


async def test_extract_with_env_context_attaches_full_dict():
    ext = FiveW1HExtractor(_router(_valid_llm_response()))
    env = {"city": "Tokyo", "scene": "kitchen"}
    result = await ext.extract("I need to call the dentist", env_context=env)
    assert result is not None
    assert result.context == env


# ── Slice 12: env_context=None → context stays {} ────────────────────────────

async def test_extract_with_none_env_context_has_empty_context():
    ext = FiveW1HExtractor(_router(_valid_llm_response()))
    result = await ext.extract("I need to call the dentist", env_context=None)
    assert result is not None
    assert result.context == {}
