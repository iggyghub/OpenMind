"""
Home Assistant MCP plugin — Issue #76, ADR-0005.

Tools:
  - homeassistant_list_entities(domain?) — list entities, optionally filtered.
  - homeassistant_get_state(entity_id) — return a single entity's state.
  - homeassistant_call_service(domain, service, target_entity_id?, data?) —
    fire a service call (turn_on, lock, set_temperature, …).

Auth is a Long-Lived Access Token in HOMEASSISTANT_TOKEN. Base URL in
HOMEASSISTANT_URL (default http://homeassistant.local:8123). Tokens missing →
the plugin returns the canonical "Set HOMEASSISTANT_TOKEN…" string with zero
HTTP fired; an HA on the LAN is the only configurable surface.

Design decisions are locked in issue #76's sharpener comment — see that for
the rationale on capability declaration, error-string vocabulary, idempotent
soft-warning, and the deliberate registration-time ping (the one deviation
from the codebase's lazy-on-construct precedent).
"""
import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any, Awaitable, Callable

from cerebral.mcp.orchestrator import Tool, ToolResult

logger = logging.getLogger(__name__)

PLUGIN_NAME = "homeassistant"

# ADR-0005 / Issue #76 — homeassistant_list_entities + homeassistant_get_state
# hit the local HA REST API; homeassistant_call_service additionally drives
# physical devices (lights, locks, switches, climate, …). No secrets_read:
# the LLAT is used internally for authentication, never returned to the LLM
# (mirrors n8n.py:24 / github.py:30-34 — bitwarden.py:40 is the only
# secrets_read declarer because its tools surface vault items to the LLM).
REQUIRED_CAPABILITIES: frozenset[str] = frozenset({
    "network_egress_local", "device_control",
})

_DEFAULT_BASE_URL = "http://homeassistant.local:8123"
_MISSING_TOKEN_MSG = "Set HOMEASSISTANT_TOKEN to use Home Assistant"
_AUTH_FAILED_MSG = "Home Assistant rejected the token"
_CONNECT_FAILED_MSG = "Could not connect to Home Assistant"
_GENERIC_ERROR_MSG = "Home Assistant error"
_IDEMPOTENT_WARNING = "Service ran but no entities changed"
_REGISTRATION_TIMEOUT_SEC = 2
_HTTP_TIMEOUT_SEC = 5

FetchFn = Callable[..., Awaitable[Any]]


async def _default_fetch(
    method: str,
    url: str,
    *,
    headers: dict | None = None,
    json: dict | None = None,
) -> Any:
    """HTTP fetch with raise_for_status — caller branches on the resulting
    HTTPStatusError / RequestError for HA's canonical error vocabulary.

    Mirrors plugins/n8n.py:32-47 with three deliberate deltas:
      1. Return type widened to dict | list (HA /api/states returns an array).
      2. Explicit timeout=5 at the client level (HA can be slow on a big LAN).
      3. raise_for_status() so the helper can branch on .status_code.
    """
    try:
        import aiohttp  # type: ignore

        timeout = aiohttp.ClientTimeout(total=_HTTP_TIMEOUT_SEC)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.request(
                method, url, headers=headers, json=json
            ) as resp:
                resp.raise_for_status()
                return await resp.json()
    except ImportError:
        pass
    try:
        import httpx  # type: ignore

        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SEC) as client:
            resp = await client.request(method, url, headers=headers, json=json)
            resp.raise_for_status()
            return resp.json()
    except ImportError:
        pass
    raise RuntimeError(
        "Neither aiohttp nor httpx is installed — cannot make HTTP requests"
    )


def _status_code(exc: Exception) -> int | None:
    """Pull the HTTP status code out of an httpx or aiohttp exception."""
    response = getattr(exc, "response", None)
    if response is not None:
        code = getattr(response, "status_code", None)
        if isinstance(code, int):
            return code
    code = getattr(exc, "status", None)
    if isinstance(code, int):
        return code
    return None


def _is_connect_or_timeout(exc: Exception) -> bool:
    """True for connect / DNS / timeout failures across httpx and aiohttp."""
    try:
        import httpx  # type: ignore
    except ImportError:
        httpx = None  # type: ignore
    if httpx is not None and isinstance(exc, httpx.RequestError) and not isinstance(
        exc, httpx.HTTPStatusError
    ):
        return True
    try:
        import aiohttp  # type: ignore
    except ImportError:
        aiohttp = None  # type: ignore
    if aiohttp is not None:
        if isinstance(
            exc,
            (
                aiohttp.ClientConnectionError,
                aiohttp.ServerTimeoutError,
            ),
        ):
            return True
    import asyncio
    if isinstance(exc, asyncio.TimeoutError):
        return True
    return False


class HomeAssistantPlugin:
    name = PLUGIN_NAME

    def __init__(
        self,
        fetch_fn: FetchFn | None = None,
        base_url: str | None = None,
        token: str | None = None,
    ) -> None:
        self._fetch = fetch_fn or _default_fetch
        self._base_url = (
            base_url or os.environ.get("HOMEASSISTANT_URL", _DEFAULT_BASE_URL)
        ).rstrip("/")
        self._token = (
            token if token is not None else os.environ.get("HOMEASSISTANT_TOKEN", "")
        )
        # Sharpener §9 — registration-time ping (the one deviation from
        # the codebase's lazy-on-construct precedent). Visible startup
        # feedback when HA is unreachable; tools still register and
        # return errors until HA comes up.
        self._ping_at_registration()

    def _ping_at_registration(self) -> None:
        try:
            with urllib.request.urlopen(
                f"{self._base_url}/api/", timeout=_REGISTRATION_TIMEOUT_SEC
            ):
                pass
        except Exception:
            logger.warning(
                "[homeassistant] Could not connect to Home Assistant at %s "
                "during registration — tools will return errors until reachable",
                self._base_url,
            )

    # ------------------------------------------------------------------
    # Plugin protocol
    # ------------------------------------------------------------------

    def list_tools(self) -> list[Tool]:
        return [
            Tool(
                name="homeassistant_list_entities",
                description=(
                    "List entities known to the local Home Assistant. "
                    "Optionally filter by domain (e.g. 'light', 'lock', 'climate')."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "domain": {
                            "type": "string",
                            "description": (
                                "Optional HA domain filter ('light', 'switch', "
                                "'climate', 'lock', 'scene', ...). Omit for all entities."
                            ),
                        },
                    },
                },
            ),
            Tool(
                name="homeassistant_get_state",
                description=(
                    "Return the current state and attributes of a single Home "
                    "Assistant entity by its fully-qualified id."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "entity_id": {
                            "type": "string",
                            "description": (
                                "Fully-qualified entity id, e.g. 'light.kitchen' "
                                "or 'lock.front_door'."
                            ),
                        },
                    },
                    "required": ["entity_id"],
                },
            ),
            Tool(
                name="homeassistant_call_service",
                description=(
                    "Call a Home Assistant service. domain+service identify it "
                    "(e.g. light.turn_on, lock.lock, scene.turn_on); "
                    "target_entity_id is the entity to act on; data carries any "
                    "extra payload (brightness, rgb_color, …)."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "domain": {
                            "type": "string",
                            "description": (
                                "HA service domain, e.g. 'light', 'lock', 'scene'."
                            ),
                        },
                        "service": {
                            "type": "string",
                            "description": (
                                "Service name within the domain, e.g. 'turn_on', "
                                "'lock', 'set_temperature'."
                            ),
                        },
                        "target_entity_id": {
                            "type": "string",
                            "description": (
                                "Entity to act on, e.g. 'light.kitchen'. Optional — "
                                "some services (scene.turn_on) take it, some don't."
                            ),
                        },
                        "data": {
                            "type": "object",
                            "description": (
                                "Extra service payload merged with target_entity_id, "
                                "e.g. {'brightness': 200}, {'rgb_color': [255, 0, 0]}."
                            ),
                        },
                    },
                    "required": ["domain", "service"],
                },
            ),
        ]

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        if tool_name == "homeassistant_list_entities":
            return await self._list_entities(args)
        if tool_name == "homeassistant_get_state":
            return await self._get_state(args)
        if tool_name == "homeassistant_call_service":
            return await self._call_service(args)
        return ToolResult(content=f"Unknown tool: '{tool_name}'", is_error=True)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _auth_headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }

    def _missing_token(self) -> ToolResult:
        return ToolResult(content=_MISSING_TOKEN_MSG, is_error=True)

    async def _list_entities(self, args: dict) -> ToolResult:
        if not self._token:
            return self._missing_token()
        url = f"{self._base_url}/api/states"
        try:
            entities = await self._fetch("GET", url, headers=self._auth_headers())
        except Exception as exc:
            return self._error_result(exc, action="list_entities", url=url)

        if not isinstance(entities, list):
            entities = []
        domain = args.get("domain")
        if domain:
            entities = [
                e for e in entities
                if isinstance(e, dict)
                and e.get("entity_id", "").split(".", 1)[0] == domain
            ]
        return ToolResult(content=json.dumps({"entities": entities}))

    async def _get_state(self, args: dict) -> ToolResult:
        if not self._token:
            return self._missing_token()
        entity_id = args.get("entity_id")
        if not entity_id:
            return ToolResult(
                content="'entity_id' is required for homeassistant_get_state",
                is_error=True,
            )
        url = f"{self._base_url}/api/states/{entity_id}"
        try:
            state = await self._fetch("GET", url, headers=self._auth_headers())
        except Exception as exc:
            return self._error_result(
                exc, action="get_state", url=url, target=entity_id
            )
        return ToolResult(content=json.dumps(state))

    async def _call_service(self, args: dict) -> ToolResult:
        if not self._token:
            return self._missing_token()
        domain = args.get("domain")
        service = args.get("service")
        if not domain or not service:
            return ToolResult(
                content="'domain' and 'service' are required for homeassistant_call_service",
                is_error=True,
            )
        body = {**(args.get("data") or {})}
        target = args.get("target_entity_id")
        if target:
            body["entity_id"] = target

        url = f"{self._base_url}/api/services/{domain}/{service}"
        target_label = f"{domain}.{service}"
        try:
            changed = await self._fetch(
                "POST", url, headers=self._auth_headers(), json=body
            )
        except Exception as exc:
            return self._error_result(
                exc, action="call_service", url=url, target=target_label
            )

        if not isinstance(changed, list):
            changed = []
        if not changed:
            # Sharpener §4 — idempotent / no-op service call is a soft warning,
            # not an error. The LLM can phrase "kitchen light is already on".
            return ToolResult(
                content=json.dumps(
                    {"changed": [], "warning": _IDEMPOTENT_WARNING}
                ),
            )
        return ToolResult(content=json.dumps({"changed": changed}))

    def _error_result(
        self,
        exc: Exception,
        *,
        action: str,
        url: str,
        target: str | None = None,
    ) -> ToolResult:
        """Map an HTTP exception to one of the six canonical strings.

        The user-facing string is TTS-short; the full detail (URL, status,
        exception) goes to the warning log so a debugging user can pull
        the cause out of stderr.
        """
        status = _status_code(exc)
        message = self._classify(action, status, target, exc)
        logger.warning(
            "[homeassistant] %s on %s — status=%s exc=%r",
            action, url, status, exc,
        )
        return ToolResult(content=message, is_error=True)

    @staticmethod
    def _classify(
        action: str, status: int | None, target: str | None, exc: Exception
    ) -> str:
        if status in (401, 403):
            return _AUTH_FAILED_MSG
        if status == 404:
            if action == "get_state" and target is not None:
                return f"Entity not found: '{target}'"
            if action == "call_service" and target is not None:
                return f"Service not found: '{target}'"
            return _GENERIC_ERROR_MSG
        if status is not None:
            # Any other non-2xx (400, 5xx, …).
            return _GENERIC_ERROR_MSG
        # No HTTP status → connect/DNS/timeout, or a non-HTTP error.
        if _is_connect_or_timeout(exc):
            return _CONNECT_FAILED_MSG
        return _GENERIC_ERROR_MSG


def create(
    fetch_fn: FetchFn | None = None,
    base_url: str | None = None,
    token: str | None = None,
) -> HomeAssistantPlugin:
    return HomeAssistantPlugin(fetch_fn=fetch_fn, base_url=base_url, token=token)
