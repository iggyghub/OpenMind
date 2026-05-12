"""
Phone MCP plugin — Issue #23.

Tools: start_call.

Phone calls go through OpenClaw — same pattern as the channel bridge in
#22. Cerebral is just an HTTP client posting to OpenClaw's outbound voice
channel; OpenClaw owns the SIP/Twilio/etc. transport.

All HTTP is injected via fetch_fn so tests run without a live OpenClaw.

Endpoint: POST <base_url>/voice/dial with JSON body
    {"contact": "Mum"}      OR      {"number": "+15551234567"}

Returns whatever OpenClaw returns (typically a call id + initial status).
"""
import json
import logging
from typing import Any, Awaitable, Callable

from cerebral.mcp.orchestrator import Tool, ToolResult

logger = logging.getLogger(__name__)

PLUGIN_NAME = "phone"

# ADR-0005 / Issue #44 — start_call POSTs a dial request to local OpenClaw
# (network_egress_local) which then places the outbound call
# (external_data_write — a phone call is a side-effecting write to the
# external phone network).
REQUIRED_CAPABILITIES: frozenset[str] = frozenset({
    "external_data_write",
    "network_egress_local",
})

DEFAULT_BASE_URL = "http://localhost:3000"

FetchFn = Callable[[str, dict], Awaitable[dict]]


async def _default_fetch(url: str, body: dict) -> dict:
    """POST body to url and return parsed JSON. Tries aiohttp then httpx."""
    try:
        import aiohttp  # type: ignore
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=body) as resp:
                return await resp.json()
    except ImportError:
        pass
    try:
        import httpx  # type: ignore
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json=body)
            return resp.json()
    except ImportError:
        pass
    raise RuntimeError("Neither aiohttp nor httpx is installed — cannot make HTTP requests")


class PhonePlugin:
    name = PLUGIN_NAME

    def __init__(
        self,
        fetch_fn: FetchFn | None = None,
        *,
        base_url: str | None = None,
    ) -> None:
        self._fetch = fetch_fn or _default_fetch
        self._base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")

    # ------------------------------------------------------------------
    # Plugin protocol
    # ------------------------------------------------------------------

    def list_tools(self) -> list[Tool]:
        return [
            Tool(
                name="start_call",
                description=(
                    "Place an outbound phone call via OpenClaw. Provide "
                    "either a contact name (resolved by OpenClaw against "
                    "the connected address book) or an E.164 phone number."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "contact": {
                            "type": "string",
                            "description": "Contact name to dial (e.g. 'Mum')",
                        },
                        "number": {
                            "type": "string",
                            "description": "Phone number in E.164 format (e.g. '+15551234567')",
                        },
                    },
                },
            ),
        ]

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        if tool_name == "start_call":
            return await self._start_call(args)
        return ToolResult(content=f"Unknown tool: '{tool_name}'", is_error=True)

    # ------------------------------------------------------------------
    # Implementation
    # ------------------------------------------------------------------

    async def _start_call(self, args: dict) -> ToolResult:
        contact = args.get("contact")
        number = args.get("number")
        if not contact and not number:
            return ToolResult(
                content="'contact' or 'number' is required for start_call",
                is_error=True,
            )

        body: dict = {}
        if contact:
            body["contact"] = contact
        if number:
            body["number"] = number

        try:
            data = await self._fetch(f"{self._base_url}/voice/dial", body)
        except Exception as exc:
            logger.error("[phone] start_call failed: %s", exc)
            return ToolResult(content=f"Call failed: {exc}", is_error=True)

        return ToolResult(content=json.dumps(data))


def create(
    fetch_fn: FetchFn | None = None,
    *,
    base_url: str | None = None,
) -> PhonePlugin:
    return PhonePlugin(fetch_fn=fetch_fn, base_url=base_url)
