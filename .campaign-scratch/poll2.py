"""Poll conversation_turns in cerebral/data/openmind.db directly for a self_dev_campaign
tool_result after a baseline turn id, without holding a WS connection open. Useful when a
long-running self_dev call outlives the WS connection that triggered it.

Usage: python poll2.py <since_id> [timeout_sec]
"""
import json
import sqlite3
import sys
import time

DB_PATH = r"C:\OpenMind\cerebral\data\openmind.db"


def main():
    since_id = int(sys.argv[1])
    timeout = int(sys.argv[2]) if len(sys.argv) > 2 else 1800
    deadline = time.time() + timeout
    seen = set()
    while time.time() < deadline:
        conn = sqlite3.connect(DB_PATH)
        try:
            rows = conn.execute(
                "SELECT id, kind, ts, content_json FROM conversation_turns "
                "WHERE id > ? ORDER BY id", (since_id,),
            ).fetchall()
        finally:
            conn.close()
        for rid, kind, ts, content_json in rows:
            if rid in seen:
                continue
            seen.add(rid)
            try:
                content = json.loads(content_json)
            except Exception:
                content = content_json
            summary = json.dumps(content)[:200]
            print(f"[{rid}] {ts} {kind}: {summary}", flush=True)
            if kind == "tool_call" and isinstance(content, dict) and content.get("name") == "self_dev_campaign":
                pass  # the original trigger, not the result
            if isinstance(content, dict):
                name = content.get("name") or (content.get("content") or {})
            if kind in ("system_event",) and isinstance(content, dict):
                if content.get("kind") in ("self_dev_pr_auto_merged", "self_dev_campaign_blocked"):
                    print("=== CAMPAIGN EVENT ===")
                    print(json.dumps(content, indent=2))
        time.sleep(5)
    print("TIMEOUT")


if __name__ == "__main__":
    main()
