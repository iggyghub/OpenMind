"""Ping the active model and report whether it's reachable. Used by
scripts/test-model.ps1. Prints a plain CONNECTED / NOT CONNECTED line so the
wrapper can colour the outcome."""
import asyncio
import sys
import time


def _build_backend(profile, mid):
    from cerebral.llm.router import (
        AnthropicBackend, DynamicModelBackend, OllamaBackend, build_custom_backend,
    )
    if mid.startswith("custom/"):
        from cerebral.db.custom_models import CustomModelStore
        from cerebral.db.credentials import CredentialStore
        row = next((r for r in CustomModelStore().list(profile.id) if r["id"] == mid), None)
        if row is None:
            return None, f"no stored config for {mid}"
        key = (CredentialStore().get_secret(profile.id, row["secret_ref"], "api_token")
               if row["secret_ref"] else None)
        if row.get("dynamic"):
            return DynamicModelBackend(
                row["kind"], row["url"], cached_model=row["model"], api_key=key,
                supports_vision=row.get("supports_vision", False),
            ), None
        b, _ = build_custom_backend(
            row["kind"], row["url"], row["model"], key, row.get("supports_vision", False))
        return b, None
    if mid.startswith("ollama/"):
        return OllamaBackend(url="http://localhost:11434", model=mid.split("/", 1)[1]), None
    if mid.startswith("claude/"):
        return AnthropicBackend(model=mid.split("/", 1)[1]), None
    return None, f"unknown model kind for {mid!r}"


def _pick_fallback(profile, exclude_mid):
    """First stored custom model (other than the stale one) whose backend
    builds. Mirrors main.py's "saved id not in current backends -> use
    default" posture without pulling in ModelRouter.

    ponytail: only scans CustomModelStore, not ollama/claude ids — those
    aren't persisted anywhere the script can read without a running
    Cerebral. Upgrade to ModelRouter.priority() if operators need ollama/
    claude fallback too.
    """
    from cerebral.db.custom_models import CustomModelStore
    for row in CustomModelStore().list(profile.id):
        if row["id"] == exclude_mid:
            continue
        backend, _err = _build_backend(profile, row["id"])
        if backend is not None:
            return row["id"], backend
    return None, None


async def _run() -> int:
    from cerebral.db.profiles import ProfileManager
    p = ProfileManager().get_active()
    if p is None:
        print("NOT CONNECTED: no active profile")
        return 1
    mid = getattr(p, "active_model", None) or "unknown"
    print(f"Active model : {mid}")
    backend, err = _build_backend(p, mid)

    ping_id = mid
    stale = False
    if backend is None:
        stale = True
        print(f"WARNING: saved active_model '{mid}' is stale ({err}); falling back")
        ping_id, backend = _pick_fallback(p, mid)
        if backend is None:
            print(f"NOT CONNECTED: no stored config for {mid} and no fallback model available")
            return 1

    print(f"Pinging endpoint ({ping_id})...")
    t0 = time.monotonic()
    try:
        reply = await backend.complete("Reply with the single word OK.", task_type="chat")
        dt = (time.monotonic() - t0) * 1000
        vis = getattr(backend, "supports_vision", False)
        print(f"CONNECTED in {dt:.0f} ms  (vision={'on' if vis else 'off'})")
        if stale:
            print(f"NOTE: fell back from stale saved id '{mid}' to '{ping_id}'")
        print(f"Reply        : {(reply or '')[:120]!r}")
        return 0
    except Exception as exc:
        print(f"NOT CONNECTED: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(_run()))
