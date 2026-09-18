"""Start (or resume) the FELIX-AUDIT self_dev campaign over Cerebral's IPC bridge.

Companion to scripts/run-felix-audit.ps1 -- that script owns the operator UX
and the driver guard rails; this one owns the one websocket round trip.

The campaign itself runs INSIDE Felix: self_dev_campaign clones, edits, tests,
opens the PR, auto-merges and rewrites the driver, one slice at a time. This
just asks it to start and waits for the tool result.

Usage:  python scripts/felix_audit_call.py <driver_path> [max_slices]
Exit:   0 campaign returned (read its JSON for status), 1 could not reach Felix.
"""
from __future__ import annotations

import asyncio
import json
import sys

URL = "ws://localhost:7766"
TOOL = "self_dev_campaign"

# self_dev_campaign runs whole slices (clone, model edit, pytest, PR, merge)
# before it returns. A single slice can legitimately take many minutes; the
# edit call alone has a 300s model timeout. No read deadline here on purpose --
# the operator's stop signal is Ctrl+C, not a timeout that abandons a slice
# mid-merge and leaves the driver mid-rewrite.
RECV_TIMEOUT = None


async def main() -> int:
    if len(sys.argv) < 2:
        print("usage: felix_audit_call.py <driver_path> [max_slices]")
        return 1
    driver = sys.argv[1]
    max_slices = int(sys.argv[2]) if len(sys.argv) > 2 else 1

    try:
        import websockets
    except ImportError:
        print("FAILED: the 'websockets' package is not importable.")
        return 1

    try:
        conn = await asyncio.wait_for(websockets.connect(URL, max_size=None), timeout=15)
    except Exception as exc:
        print(f"FAILED: cannot reach Cerebral at {URL} ({type(exc).__name__}: {exc}).")
        print("Is Felix running? Start it with scripts/launch-felix.ps1.")
        return 1

    async with conn as ws:
        await ws.send(json.dumps({
            "type": "call_tool",
            "data": {
                "name": TOOL,
                "args": {"driver_file": driver, "max_slices": max_slices},
                # Keep the campaign's own start out of the conversation
                # transcript; the slices record their own turns.
                "record": False,
            },
        }))
        print(f"campaign started: {TOOL}(driver_file={driver!r}, max_slices={max_slices})")
        print("waiting for the tool result -- a slice takes minutes, Ctrl+C to stop watching")
        print("(stopping the watch does NOT stop the campaign; it runs inside Felix)")
        sys.stdout.flush()

        # Cerebral replies to a record=False call_tool with a tool_result
        # envelope; everything else on the socket is unrelated broadcast
        # traffic for the tray, so filter rather than take the first frame.
        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=RECV_TIMEOUT)
            try:
                ev = json.loads(raw)
            except (TypeError, ValueError):
                continue
            if ev.get("type") != "tool_result":
                continue
            data = ev.get("data") or {}
            if data.get("name") not in (TOOL, None):
                continue
            content = data.get("content", "")
            print("\n--- campaign result ---")
            try:
                print(json.dumps(json.loads(content), indent=2))
            except (TypeError, ValueError):
                print(content)
            return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("\nstopped watching; the campaign continues inside Felix.")
        raise SystemExit(0)
