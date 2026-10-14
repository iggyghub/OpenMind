"""Send plain text through user_text_command, the same path as if the user typed/spoke it.
Usage: python ask_felix.py "<text>" [timeout_sec]"""
import asyncio
import json
import sys

import websockets


async def main():
    text = sys.argv[1]
    timeout_sec = int(sys.argv[2]) if len(sys.argv) > 2 else 30
    uri = "ws://localhost:7766"
    async with websockets.connect(uri, open_timeout=20) as ws:
        await ws.send(json.dumps({"type": "user_text_command", "data": {"text": text}}))
        print(f"Sent: {text!r}. Waiting up to {timeout_sec}s...", flush=True)
        deadline = asyncio.get_event_loop().time() + timeout_sec
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                print("done waiting (no more messages)")
                return
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=remaining)
            except asyncio.TimeoutError:
                print("done waiting (timeout)")
                return
            except websockets.exceptions.ConnectionClosed:
                print("connection closed (expected on restart)")
                return
            print(msg[:300], flush=True)


if __name__ == "__main__":
    asyncio.run(main())
