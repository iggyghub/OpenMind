"""Fire self_dev_campaign as a call_tool message against the live Cerebral IPC server and wait
for its tool_result. See memory reference_felix_ipc_bridge for the protocol.

Usage: python fire_campaign.py <driver_file> [max_slices] [timeout_sec]
"""
import asyncio
import json
import sys

import websockets


async def main():
    driver_file = sys.argv[1]
    max_slices = int(sys.argv[2]) if len(sys.argv) > 2 else 20
    timeout_sec = int(sys.argv[3]) if len(sys.argv) > 3 else 600

    uri = "ws://localhost:7766"
    async with websockets.connect(uri, max_size=None, open_timeout=20) as ws:
        await ws.send(json.dumps({
            "type": "call_tool",
            "data": {
                "name": "self_dev_campaign",
                "args": {"driver_file": driver_file, "max_slices": max_slices},
            },
        }))
        print(f"Sent self_dev_campaign for {driver_file} (max_slices={max_slices}). Waiting...",
              flush=True)
        deadline = asyncio.get_event_loop().time() + timeout_sec
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                print("TIMEOUT waiting for tool_result")
                return
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=remaining)
            except asyncio.TimeoutError:
                print("TIMEOUT waiting for tool_result")
                return
            try:
                parsed = json.loads(msg)
            except json.JSONDecodeError:
                continue
            if parsed.get("type") == "tool_result" and parsed.get("data", {}).get("name") == "self_dev_campaign":
                print("=== tool_result ===")
                print(json.dumps(parsed["data"], indent=2)[:8000])
                return
            # Progress breadcrumbs -- print anything that looks self_dev-related.
            t = parsed.get("type", "")
            if "self_dev" in t or "self_dev" in json.dumps(parsed)[:200]:
                print(f"[{t}]", json.dumps(parsed)[:300])


if __name__ == "__main__":
    asyncio.run(main())
