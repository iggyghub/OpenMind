"""Ask a running Cerebral to surface the Felix main window (#441).

Invoked by launch-felix.ps1 when Felix is already running: the launcher
cannot reach the hidden Electron window directly, but Cerebral can
broadcast `open_felix` to the tray over the shared WebSocket.
"""
import asyncio
import json

import websockets


async def main() -> None:
    async with websockets.connect("ws://localhost:7766") as ws:
        await ws.send(json.dumps({"type": "open_felix", "data": {}}))


if __name__ == "__main__":
    asyncio.run(main())
