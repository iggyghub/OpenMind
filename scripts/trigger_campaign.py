"""Trigger a self_dev_campaign run on a live Felix instance over its tray
WebSocket IPC (ws://localhost:7766) and print the JSON result.

Bypasses the chat/LLM layer entirely -- sends the same {"type": "call_tool"}
message the tray sends for a direct tool invocation (main.py's
_dispatch_tray_call_tool / issue #238), so there's no dependency on a model
noticing or correctly invoking the tool. Applies the same ACL/consent gate
tray calls always go through.

Usage:
    python scripts/trigger_campaign.py <driver_file> [max_slices]

Exit code 0 with the result JSON on stdout; non-zero with a message on
stderr on connection failure or timeout. A campaign result of status
"blocked" or "max_slices_reached" is still exit 0 -- that's the caller's
job to interpret, not this script's.
"""
import asyncio
import json
import sys

import websockets

WS_URL = "ws://localhost:7766"
# self_dev_campaign can run one slice for 5-40+ minutes (clone, an LLM
# edit, the full test suite, PR creation, an optional conflict retry).
# Generous on purpose -- a timeout here should mean something is actually
# stuck, not that a normal slow slice got cut off.
TIMEOUT_SECONDS = 45 * 60


async def trigger(driver_file: str, max_slices: int) -> dict:
    async with websockets.connect(WS_URL, max_size=None) as ws:
        await ws.recv()  # initial greet payload -- ignored, just drains it

        await ws.send(json.dumps({
            "type": "call_tool",
            "data": {
                "name": "self_dev_campaign",
                "args": {"driver_file": driver_file, "max_slices": max_slices},
            },
        }))

        deadline = asyncio.get_event_loop().time() + TIMEOUT_SECONDS
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                raise TimeoutError(
                    f"No self_dev_campaign tool_result within {TIMEOUT_SECONDS}s"
                )
            raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if msg.get("type") != "tool_result":
                continue
            data = msg.get("data") or {}
            if data.get("name") != "self_dev_campaign":
                continue
            if data.get("is_error"):
                raise RuntimeError(f"self_dev_campaign call errored: {data.get('content')}")
            return json.loads(data["content"])


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: trigger_campaign.py <driver_file> [max_slices]", file=sys.stderr)
        return 2
    driver_file = sys.argv[1]
    max_slices = int(sys.argv[2]) if len(sys.argv) > 2 else 1

    try:
        result = asyncio.run(trigger(driver_file, max_slices))
    except Exception as exc:
        print(f"trigger_campaign failed: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
