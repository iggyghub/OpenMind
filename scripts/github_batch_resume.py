"""Periodic Budd-requeue nudge for the GitHub ingest batch (ADR-0019 S4).

Reingests repos that were requeued off a Budd failure (budd_requeues > 0),
Budd-first. `_ingest_repo` drains a repo to a local model automatically once it
has been requeued DRAIN_AT_REQUEUES (3) times, so a persistently-down Budd can't
wedge a repo forever. Idempotent -- already-extracted docs skip. Safe to run by
hand; no-ops if Cerebral (ws://127.0.0.1:7766) is down.
"""
import asyncio
import json
import sqlite3
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "cerebral" / "data" / "openmind.db"


def _repos_needing_retry() -> list[str]:
    try:
        con = sqlite3.connect(DB)
        rows = con.execute(
            "SELECT repo_url FROM github_repos WHERE budd_requeues > 0 ORDER BY updated_at"
        ).fetchall()
        con.close()
        return [r[0] for r in rows]
    except Exception:
        return []


async def _main() -> None:
    import websockets

    repos = _repos_needing_retry()
    if not repos:
        print("nothing to retry")
        return
    try:
        async with websockets.connect("ws://127.0.0.1:7766", open_timeout=10) as ws:
            for url in repos:
                await ws.send(json.dumps({"type": "call_tool", "data": {
                    "name": "github_reingest", "args": {"repo_url": url},
                }}))
                await asyncio.sleep(1)
        print(f"nudged {len(repos)} repo(s): {repos}")
    except Exception as exc:  # Cerebral down -- try again next cycle
        print(f"skip: Cerebral unreachable ({exc})")


if __name__ == "__main__":
    asyncio.run(_main())
