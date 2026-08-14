import os
import pytest

from cerebral.session.store import save_checkpoint, restore_checkpoint


class MockState:
    def __init__(self, sid: str = "test-session"):
        self.session_id = sid
        self.current_task_id = "task-1"
        self.in_flight_tool_calls = [{"tool": "search", "args": {"q": "test"}}]
        self.memory_context = ["fact-1"]
        self.conversation_step = 5


def test_save_then_restore(tmp_path, monkeypatch):
    base_dir = str(tmp_path / ".cerebral")
    monkeypatch.setattr("os.path.expanduser", lambda x: base_dir if x == "~/.cerebral" else os.path.expanduser(x))

    state = MockState("rt-test")
    path = save_checkpoint(state)
    assert path.endswith("rt-test/checkpoint.json")
    
    restored = restore_checkpoint("rt-test")
    assert restored is not None
    assert restored["session_id"] == "rt-test"
    assert restored["current_task_id"] == "task-1"
    assert restored["conversation_step"] == 5


def test_restore_no_checkpoint(tmp_path, monkeypatch):
    base_dir = str(tmp_path / ".cerebral")
    monkeypatch.setattr("os.path.expanduser", lambda x: base_dir if x == "~/.cerebral" else os.path.expanduser(x))
    
    restored = restore_checkpoint("ghost-session")
    assert restored is None


def test_checkpoint_dir_creation(tmp_path, monkeypatch):
    base_dir = str(tmp_path / ".cerebral")
    monkeypatch.setattr("os.path.expanduser", lambda x: base_dir if x == "~/.cerebral" else os.path.expanduser(x))
    
    state = MockState("dir-test")
    save_checkpoint(state)
    
    expected_dir = tmp_path / ".cerebral" / "sessions" / "dir-test"
    assert expected_dir.exists()
    assert (expected_dir / "checkpoint.json").exists()
