"""Tests that exercise real audio libraries. Skipped when the heavy runtime deps are absent."""

import io

import numpy as np
import pytest

from voice_ai import speech


def _encode(samples: np.ndarray, rate: int, container: str, codec: str) -> bytes:
    av = pytest.importorskip("av")
    buf = io.BytesIO()
    with av.open(buf, mode="w", format=container) as out:
        stream = out.add_stream(codec, rate=rate)
        stream.layout = "mono"
        frame = av.AudioFrame.from_ndarray(
            (samples * 32767).astype(np.int16).reshape(1, -1), format="s16", layout="mono"
        )
        frame.sample_rate = rate
        for packet in stream.encode(frame):
            out.mux(packet)
        for packet in stream.encode(None):
            out.mux(packet)
    return buf.getvalue()


def _tone(seconds: float, rate: int) -> np.ndarray:
    t = np.arange(int(seconds * rate)) / rate
    return (0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)


def test_decode_browser_webm_opus_recording():
    pytest.importorskip("av")
    webm = _encode(_tone(1.0, 48000), 48000, "webm", "libopus")
    data, rate = speech.decode_audio(webm)
    assert rate == speech.TARGET_SAMPLE_RATE
    assert abs(len(data) - rate) < rate * 0.1
    assert np.abs(data).max() > 0.1


def test_mp3_tts_output_to_pcm16_for_live_call():
    pytest.importorskip("av")
    mp3 = _encode(_tone(0.5, 24000), 24000, "mp3", "mp3")
    pcm = speech.encoded_audio_to_pcm16(mp3, 24000)
    assert len(pcm) % 2 == 0
    assert abs(len(pcm) / 2 - 12000) < 2400


def test_wav_tts_output_is_resampled_for_live_call():
    pytest.importorskip("librosa")
    import soundfile as sf

    buf = io.BytesIO()
    sf.write(buf, _tone(0.5, 22050), 22050, format="WAV", subtype="PCM_16")
    pcm = speech.encoded_audio_to_pcm16(buf.getvalue(), 24000)
    assert abs(len(pcm) / 2 - 12000) < 50


def test_silero_vad_rejects_silence():
    pytest.importorskip("silero_vad")
    state = speech.ModelState(device="cpu")
    speech.load_vad(state)
    result = speech.run_vad(state, np.zeros(16000, dtype=np.float32), 16000)
    assert result["has_speech"] is False
    result = speech.run_vad(state, np.zeros(48000, dtype=np.float32), 48000)  # resampled path
    assert result["has_speech"] is False
