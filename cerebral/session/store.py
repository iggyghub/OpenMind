from __future__ import annotations

import json
import os
from datetime import datetime
from typing import TYPE_CHECKING, Any, TypedDict

if TYPE_CHECKING:
    from cerebral.orchestrator.session import SessionState

class SessionCheckpoint(TypedDict):
    session_id: str
    timestamp: str
    current_task_id: str | None
    in_flight_tool_calls: list[dict[str, Any]]
    memory_context: list[str]
    conversation_step: int

def save_checkpoint(session_state: "SessionState") -> str:
    sid = session_state.session_id
    checkpoint: SessionCheckpoint = {
        "session_id": sid,
        "timestamp": datetime.utcnow().isoformat(),
        "current_task_id": getattr(session_state, "current_task_id", None),
        "in_flight_tool_calls": getattr(session_state, "in_flight_tool_calls", []),
        "memory_context": getattr(session_state, "memory_context", []),
        "conversation_step": getattr(session_state, "conversation_step", 0),
    }
    
    session_dir = os.path.expanduser(f"~/.cerebral/sessions/{sid}")
    os.makedirs(session_dir, exist_ok=True)
    
    path = os.path.join(session_dir, "checkpoint.json")
    with open(path, "w") as f:
        json.dump(checkpoint, f, indent=2)
    return path

def restore_checkpoint(session_id: str) -> SessionCheckpoint | None:
    path = os.path.expanduser(f"~/.cerebral/sessions/{session_id}/checkpoint.json")
    if not os.path.exists(path):
        return None
    with open(path, "r") as f:
        return json.load(f)
