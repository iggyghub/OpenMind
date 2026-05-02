"""
Environmental context — Issue #14.

Provides rough location (IP geolocation) and optional camera scene inference.
All state is RAM-only; no frames or coordinates are written to disk.

Public interface:
  env = EnvironmentContext()
  await env.refresh_location()        # call at startup / network change
  env.enable_camera(interval_seconds=30)
  env.disable_camera()
  env.get_context()  # → {city, country, lat, lon, scene, camera_enabled}
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)

_GEOLOCATION_URL = "http://ip-api.com/json/?fields=status,city,country,lat,lon"


def _default_http_get(url: str) -> dict:
    import urllib.request
    import json
    with urllib.request.urlopen(url, timeout=5) as resp:
        return json.loads(resp.read())


class EnvironmentContext:
    def __init__(
        self,
        http_get: Callable[[str], dict] | None = None,
        capture_fn: Callable[[], bytes] | None = None,
        infer_fn: Callable[[bytes], str] | None = None,
        interval_seconds: float = 30.0,
    ) -> None:
        self._http_get = http_get or _default_http_get
        self._capture_fn = capture_fn
        self._infer_fn = infer_fn
        self._interval_seconds = interval_seconds

        # RAM-only state
        self._city: str = ""
        self._country: str = ""
        self._lat: float | None = None
        self._lon: float | None = None
        self._scene: str = ""
        self._camera_enabled: bool = False

        self._capture_task: asyncio.Task | None = None

    # ── Location ──────────────────────────────────────────────────────────────

    async def refresh_location(self) -> dict:
        try:
            data = await asyncio.get_event_loop().run_in_executor(
                None, lambda: self._http_get(_GEOLOCATION_URL)
            )
            if data.get("status") != "success":
                logger.warning("[env] Geolocation returned non-success status")
                return self.get_context()
            self._city = data.get("city", "")
            self._country = data.get("country", "")
            self._lat = data.get("lat")
            self._lon = data.get("lon")
            logger.info("[env] Location: %s, %s", self._city, self._country)
        except Exception:
            logger.warning("[env] Geolocation failed — running without location")
        return self.get_context()

    # ── Camera ────────────────────────────────────────────────────────────────

    @property
    def camera_enabled(self) -> bool:
        return self._camera_enabled

    def enable_camera(self, interval_seconds: float | None = None) -> None:
        if interval_seconds is not None:
            self._interval_seconds = interval_seconds
        self._camera_enabled = True
        if self._capture_fn and self._infer_fn:
            try:
                loop = asyncio.get_event_loop()
                self._capture_task = loop.create_task(self._capture_loop())
            except RuntimeError:
                # No running event loop (e.g. in sync context) — loop started later
                pass

    def disable_camera(self) -> None:
        self._camera_enabled = False
        if self._capture_task and not self._capture_task.done():
            self._capture_task.cancel()
        self._capture_task = None

    async def _capture_loop(self) -> None:
        while self._camera_enabled:
            try:
                loop = asyncio.get_event_loop()
                frame = await loop.run_in_executor(None, self._capture_fn)
                scene = await loop.run_in_executor(None, self._infer_fn, frame)
                self._scene = scene
                logger.debug("[env] Scene inferred: %s", scene)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.warning("[env] Camera capture/infer failed")
            await asyncio.sleep(self._interval_seconds)

    # ── Context snapshot ──────────────────────────────────────────────────────

    def get_context(self) -> dict:
        return {
            "city": self._city,
            "country": self._country,
            "lat": self._lat if self._lat is not None else "",
            "lon": self._lon if self._lon is not None else "",
            "scene": self._scene,
            "camera_enabled": self._camera_enabled,
        }
