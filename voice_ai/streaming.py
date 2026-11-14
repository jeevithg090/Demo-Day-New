"""Turn detection for the live-call WebSocket.

Incoming audio is raw mono PCM16. A lightweight energy-based voice activity
check buffers speech and emits a `turn_complete` event once the caller has
been silent for `silence_threshold` seconds.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

DEFAULT_ENERGY_THRESHOLD = 0.01
DEFAULT_SILENCE_SECONDS = 0.5
MAX_TURN_BYTES = 16000 * 2 * 30  # ~30 s of 16 kHz PCM16; protects memory


def chunk_energy(chunk: bytes) -> float:
    """Mean absolute amplitude of a PCM16 chunk, normalised to [0, 1]."""
    usable = len(chunk) - (len(chunk) % 2)
    if usable <= 0:
        return 0.0
    samples = np.frombuffer(chunk[:usable], dtype=np.int16).astype(np.float32) / 32768.0
    return float(np.mean(np.abs(samples)))


class StreamingSession:
    """State for a single live voice call."""

    def __init__(
        self,
        session_id: str,
        language: str = "en",
        energy_threshold: float = DEFAULT_ENERGY_THRESHOLD,
        silence_threshold: float = DEFAULT_SILENCE_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.session_id = session_id
        self.language = language
        self.state = "listening"  # listening | thinking | speaking
        self.energy_threshold = energy_threshold
        self.silence_threshold = silence_threshold
        self.last_speech_time = 0.0
        self.speech_chunks: list[bytes] = []
        self._buffered_bytes = 0
        self._clock = clock

    def reset_for_new_turn(self) -> None:
        self.speech_chunks = []
        self._buffered_bytes = 0

    def process_incoming_audio(self, chunk: bytes) -> dict[str, Any] | None:
        """Feed one audio chunk; returns an event dict or None.

        Events:
            {"type": "interrupt"}                    caller spoke while the AI was speaking
            {"type": "turn_complete", "audio": b""}  caller finished an utterance
        """
        now = self._clock()
        if chunk_energy(chunk) > self.energy_threshold:
            self.last_speech_time = now
            if self._buffered_bytes + len(chunk) <= MAX_TURN_BYTES:
                self.speech_chunks.append(chunk)
                self._buffered_bytes += len(chunk)
            if self.state == "speaking":
                logger.info("Barge-in detected (session %s)", self.session_id)
                self.state = "listening"
                return {"type": "interrupt"}
            return None

        if self.speech_chunks and now - self.last_speech_time > self.silence_threshold:
            audio = b"".join(self.speech_chunks)
            self.reset_for_new_turn()
            self.state = "thinking"
            return {"type": "turn_complete", "audio": audio}
        return None
