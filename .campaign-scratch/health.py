"""Quick health check against the live Cerebral IPC server (ws://localhost:7766).
See memory reference_felix_ipc_bridge for the protocol this follows."""
import asyncio
import json
import sys

import websockets


async def main():
    uri = "ws://localhost:7766"
    async with websockets.connect(uri) as ws:
        await ws.send(json.dumps({"type": "health_check", "data": {}}))
        for _ in range(5):
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=5)
                print(msg[:500])
            except asyncio.TimeoutError:
                break


if __name__ == "__main__":
    asyncio.run(main())
