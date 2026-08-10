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


async def _run() -> int:
    from cerebral.db.profiles import ProfileManager
    p = ProfileManager().get_active()
    if p is None:
        print("NOT CONNECTED: no active profile")
        return 1
    mid = getattr(p, "active_model", None) or "unknown"
    print(f"Active model : {mid}")
    backend, err = _build_backend(p, mid)
    if backend is None:
        print(f"NOT CONNECTED: {err}")
        return 1
    print("Pinging endpoint...")
    t0 = time.monotonic()
    try:
        reply = await backend.complete("Reply with the single word OK.", task_type="chat")
        dt = (time.monotonic() - t0) * 1000
        vis = getattr(backend, "supports_vision", False)
        print(f"CONNECTED in {dt:.0f} ms  (vision={'on' if vis else 'off'})")
        print(f"Reply        : {(reply or '')[:120]!r}")
        return 0
    except Exception as exc:
        print(f"NOT CONNECTED: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(_run()))
