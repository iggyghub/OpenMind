"""
RAM-only circular audio buffer.  Never touches disk.
Holds the last `seconds` of int16 PCM audio at `sample_rate` Hz.
"""

from __future__ import annotations

import collections
from typing import Sequence

import numpy as np

SAMPLE_RATE = 16_000
BUFFER_SECONDS = 60


class RollingBuffer:
    """
    Stores the most recent `seconds` of audio as a rolling window of chunks.
    Thread-safe reads via snapshot(); writes (extend) happen only in the
    sounddevice callback thread so no lock is needed there.
    """

    def __init__(self, sample_rate: int = SAMPLE_RATE, seconds: int = BUFFER_SECONDS) -> None:
        self._capacity: int = sample_rate * seconds
        self._chunks: collections.deque[np.ndarray] = collections.deque()
        self._total: int = 0

    def extend(self, chunk: np.ndarray) -> None:
        """Append a chunk of int16 samples, evicting old chunks to stay within capacity."""
        self._chunks.append(chunk.copy())
        self._total += len(chunk)
        while self._total > self._capacity and len(self._chunks) > 1:
            evicted = self._chunks.popleft()
            self._total -= len(evicted)

    def snapshot(self) -> np.ndarray:
        """Return a contiguous int16 array of everything currently in the buffer."""
        if not self._chunks:
            return np.array([], dtype=np.int16)
        return np.concatenate(list(self._chunks)).astype(np.int16)

    def clear(self) -> None:
        self._chunks.clear()
        self._total = 0

    def __len__(self) -> int:
        return self._total
