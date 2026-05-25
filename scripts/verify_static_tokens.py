"""
Headless live-verify for #148 static-token plugin chain.

Connects to Cerebral's IPC (ws://localhost:7766) and exercises:
- Layer 1: plugin -> live API for youtube/todoist/notion/toggl (env-fallback path)
- Layer 2: set_static_token / clear_static_token / keyring-overrides-env

Requires Cerebral already running with YOUTUBE_API_KEY, TODOIST_API_TOKEN,
NOTION_API_TOKEN, TOGGL_API_TOKEN env vars set in its process.

Run from a fresh PowerShell window (no env vars needed here):
    python verify_static_tokens.py
"""

import asyncio
import json
import sys

try:
    import websockets
except ImportError:
    print("websockets not installed in this Python. Run:")
    print("  python -m pip install websockets")
    sys.exit(1)

URL = "ws://localhost:7766"


async def _send(ws, msg):
    await ws.send(json.dumps(msg))


async def _drain_initial(ws, settle=0.5):
    """Drain any state-setup broadcasts that fire on connect."""
    end = asyncio.get_event_loop().time() + settle
    while True:
        remaining = end - asyncio.get_event_loop().time()
        if remaining <= 0:
            return
        try:
            await asyncio.wait_for(ws.recv(), timeout=remaining)
        except asyncio.TimeoutError:
            return


async def _wait_for(ws, predicate, timeout=20):
    """Wait for a message matching predicate. Auto-answer consent prompts."""
    loop = asyncio.get_event_loop()
    end = loop.time() + timeout
    while True:
        remaining = end - loop.time()
        if remaining <= 0:
            raise TimeoutError("timeout waiting for matching message")
        raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
        msg = json.loads(raw)
        t = msg.get("type")
        if t == "consent_request":
            req_id = msg.get("data", {}).get("request_id", "")
            await _send(ws, {
                "type": "consent_response",
                "data": {"request_id": req_id, "choice": "allow_session"},
            })
            continue
        if t == "irreversible_modal_request":
            req_id = msg.get("data", {}).get("request_id", "")
            await _send(ws, {
                "type": "irreversible_modal_response",
                "data": {"request_id": req_id, "choice": "confirm"},
            })
            continue
        if predicate(msg):
            return msg


async def _call_tool(ws, name, args):
    await _send(ws, {"type": "call_tool", "data": {"name": name, "args": args}})
    return await _wait_for(
        ws,
        lambda m: m.get("type") == "tool_result"
        and m.get("data", {}).get("name") == name,
    )


async def _ensure_profile(ws):
    await _send(ws, {"type": "list_profiles"})
    msg = await _wait_for(ws, lambda m: m.get("type") == "profiles_list")
    profiles = msg.get("data", {}).get("profiles", [])
    if profiles:
        print(f"[setup] Existing profile(s): {[p.get('name') for p in profiles]}")
        return
    print("[setup] No profiles -> creating 'VerifyBot'")
    await _send(ws, {
        "type": "create_profile",
        "data": {"name": "VerifyBot", "wake_name": "felix"},
    })
    await _wait_for(ws, lambda m: m.get("type") == "profile_loaded")
    print("[setup] Profile created")


def _truncate(s, n=240):
    s = s.replace("\n", " ")
    return s if len(s) <= n else s[:n] + "..."


async def main():
    print(f"Connecting to {URL} ...")
    async with websockets.connect(URL) as ws:
        await _drain_initial(ws)
        await _ensure_profile(ws)

        # ---- Layer 1: plugin -> live API (env-fallback path) ----
        tests = [
            ("youtube_search", {"query": "claude code", "max_results": 1}),
            ("todoist_list_tasks", {}),
            ("notion_search", {"query": "felix"}),
            ("toggl_list_workspaces", {}),
        ]
        layer1 = {}
        for name, args in tests:
            print(f"\n[layer-1] -> {name} {args}")
            try:
                r = await _call_tool(ws, name, args)
                is_err = r.get("data", {}).get("is_error")
                content = r.get("data", {}).get("content")
                snippet = _truncate(json.dumps(content))
                layer1[name] = (not is_err, snippet)
                print(f"[layer-1] {'FAIL' if is_err else 'OK'}: {snippet}")
            except Exception as e:
                layer1[name] = (False, repr(e))
                print(f"[layer-1] ERROR: {e!r}")

        # ---- Layer 2: keyring-overrides-env + clear-falls-back ----
        # Use notion since its env var is set; deliberately poison the keyring.
        print("\n[layer-2] keyring-overrides-env test on notion")

        print("[layer-2] step 1: set keyring=BAD_TOKEN_DELIBERATE_VERIFY")
        await _send(ws, {
            "type": "set_static_token",
            "data": {"provider": "notion", "value": "BAD_TOKEN_DELIBERATE_VERIFY"},
        })
        await asyncio.sleep(0.6)

        print("[layer-2] step 2: notion_search should now FAIL (keyring beats env)")
        r2 = await _call_tool(ws, "notion_search", {"query": "felix"})
        is_err2 = r2.get("data", {}).get("is_error")
        snippet2 = _truncate(json.dumps(r2.get("data", {}).get("content")))
        layer2_overrides = bool(is_err2)
        print(f"[layer-2] is_error={is_err2}: {snippet2}")

        print("[layer-2] step 3: clear_static_token for notion")
        await _send(ws, {
            "type": "clear_static_token",
            "data": {"provider": "notion"},
        })
        await asyncio.sleep(0.6)

        print("[layer-2] step 4: notion_search should now WORK (falls back to env)")
        r3 = await _call_tool(ws, "notion_search", {"query": "felix"})
        is_err3 = r3.get("data", {}).get("is_error")
        snippet3 = _truncate(json.dumps(r3.get("data", {}).get("content")))
        layer2_fallback = not bool(is_err3)
        print(f"[layer-2] is_error={is_err3}: {snippet3}")

        # ---- Summary ----
        print("\n" + "=" * 64)
        print("VERIFICATION SUMMARY")
        print("=" * 64)
        for name, (ok, _) in layer1.items():
            print(f"  layer-1  {name:32s} {'PASS' if ok else 'FAIL'}")
        print(f"  layer-2  keyring-overrides-env             "
              f"{'PASS' if layer2_overrides else 'FAIL'}")
        print(f"  layer-2  clear-falls-back-to-env           "
              f"{'PASS' if layer2_fallback else 'FAIL'}")
        print("=" * 64)


if __name__ == "__main__":
    asyncio.run(main())
