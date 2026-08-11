"""GPU-first whisper model loading -- S17 #673.

No real GPU/CUDA: faster_whisper.WhisperModel is monkeypatched. Verifies GPU is
tried first, a CUDA failure falls back to CPU, and the model is cached.
"""
from __future__ import annotations

import faster_whisper
import cerebral.main as m


def _reset():
    m._whisper_model = None
    m._whisper_model_key = None


def test_gpu_first_cpu_fallback_and_caching(monkeypatch):
    _reset()
    calls: list = []

    class FakeModel:
        def __init__(self, name, device, compute_type):
            calls.append((device, compute_type))
            if device == "cuda":
                raise RuntimeError("no cuda here")

    monkeypatch.setattr(faster_whisper, "WhisperModel", FakeModel)
    monkeypatch.setattr(m, "_setup_cuda_dll_path", lambda: None)

    class _S:
        @staticmethod
        def get(k, *a):
            return {"video_whisper_device": "cuda", "video_whisper_compute": "int8"}.get(k)

    monkeypatch.setattr(m, "_settings", _S())

    model = m._get_whisper_model()
    assert model is not None
    assert ("cuda", "int8") in calls, "GPU must be tried first"
    assert ("cpu", "int8") in calls, "must fall back to CPU on cuda failure"

    calls.clear()
    again = m._get_whisper_model()
    assert again is model      # cached instance
    assert calls == []          # not reloaded


def test_cpu_when_device_setting_is_cpu(monkeypatch):
    _reset()
    calls: list = []

    class FakeModel:
        def __init__(self, name, device, compute_type):
            calls.append((device, compute_type))

    monkeypatch.setattr(faster_whisper, "WhisperModel", FakeModel)

    class _S:
        @staticmethod
        def get(k, *a):
            return {"video_whisper_device": "cpu"}.get(k)

    monkeypatch.setattr(m, "_settings", _S())

    m._get_whisper_model()
    assert calls == [("cpu", "int8")]  # never touched cuda
