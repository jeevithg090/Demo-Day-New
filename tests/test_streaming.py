import numpy as np

from voice_ai.streaming import StreamingSession, chunk_energy


def pcm(amplitude: float, n: int = 1600) -> bytes:
    return (np.full(n, amplitude) * 32767).astype("<i2").tobytes()


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_chunk_energy():
    assert chunk_energy(b"") == 0.0
    assert chunk_energy(pcm(0.0)) == 0.0
    assert abs(chunk_energy(pcm(0.5)) - 0.5) < 1e-3
    assert chunk_energy(b"\x01") == 0.0  # odd-length input is tolerated


def test_turn_completes_after_silence():
    clock = FakeClock()
    session = StreamingSession("s", clock=clock, silence_threshold=0.5)

    assert session.process_incoming_audio(pcm(0.0)) is None  # silence before speech
    assert session.process_incoming_audio(pcm(0.3)) is None
    clock.now = 0.1
    assert session.process_incoming_audio(pcm(0.3)) is None
    clock.now = 0.3
    assert session.process_incoming_audio(pcm(0.0)) is None  # silence, but not long enough
    clock.now = 0.7
    event = session.process_incoming_audio(pcm(0.0))
    assert event["type"] == "turn_complete"
    assert event["audio"] == pcm(0.3) * 2
    assert session.state == "thinking"
    assert session.speech_chunks == []


def test_barge_in_while_speaking():
    session = StreamingSession("s", clock=FakeClock())
    session.state = "speaking"
    assert session.process_incoming_audio(pcm(0.3)) == {"type": "interrupt"}
    assert session.state == "listening"
