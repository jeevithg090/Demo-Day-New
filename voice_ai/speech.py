"""Speech and language model wrappers: VAD, STT, LLM and TTS.

Heavy dependencies (torch, faster-whisper, google-genai, edge-tts, piper) are
imported lazily inside the functions that need them, so the rest of the app
(admin API, session API, signal extraction) can be imported and tested without
them installed.
"""

from __future__ import annotations

import base64
import io
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .settings import Settings

logger = logging.getLogger(__name__)

TARGET_SAMPLE_RATE = 16000

EDGE_VOICES = {
    "kn": "kn-IN-GaganNeural",
    "ta": "ta-IN-PallaviNeural",
    "te": "te-IN-ShrutiNeural",
    "hi": "hi-IN-SwaraNeural",
    "en": "en-IN-NeerjaNeural",
}
DEFAULT_EDGE_VOICE = EDGE_VOICES["en"]

PIPER_VOICES = {
    "hi": "hi_IN-rohan-medium",
    "te": "te_IN-maya-medium",
}

LLM_FALLBACK_RESPONSE = (
    "I apologize, but I'm having trouble connecting to my knowledge base right now. "
    "Please call our helpline for immediate assistance."
)


class LLMUnavailableError(RuntimeError):
    """Raised when the LLM is not configured or the API call fails."""


@dataclass
class ModelState:
    """Holds loaded models. Populated by `load_models()`."""

    device: str | None = None
    vad_model: Any = None
    vad_utils: Any = None
    whisper_model: Any = None
    llm_client: Any = None
    llm_model_name: str = ""
    piper_voices: dict[str, Any] = field(default_factory=dict)
    loaded: bool = False
    loading: bool = False
    error: str | None = None
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def status(self) -> str:
        if self.loaded:
            return "healthy"
        if self.error:
            return "error"
        return "loading"


# ============================================================================
# LOADING
# ============================================================================


def _elapsed(start: float) -> str:
    return f"{time.perf_counter() - start:.2f}s"


def load_vad(state: ModelState) -> None:
    """Load Silero VAD from the `silero-vad` package (bundled weights), else torch.hub."""
    start = time.perf_counter()
    try:
        from silero_vad import get_speech_timestamps, load_silero_vad

        model, utils = load_silero_vad(), (get_speech_timestamps,)
    except ImportError:
        import torch

        model, utils = torch.hub.load(
            repo_or_dir="snakers4/silero-vad", model="silero_vad", force_reload=False, onnx=False
        )
    state.vad_model = model.to(state.device)
    state.vad_utils = utils
    logger.info("Silero VAD loaded in %s", _elapsed(start))


def load_whisper(state: ModelState, model_name: str) -> None:
    from faster_whisper import WhisperModel

    start = time.perf_counter()
    if state.device == "cuda":
        state.whisper_model = WhisperModel(model_name, device="cuda", compute_type="float16")
    else:
        state.whisper_model = WhisperModel(model_name, device="cpu", compute_type="int8")
    logger.info("Faster-Whisper '%s' loaded in %s", model_name, _elapsed(start))


def load_llm(state: ModelState, settings: Settings) -> None:
    """Create the Gemini client. Without an API key the LLM stays disabled."""
    state.llm_model_name = settings.gemini_model
    if not settings.gemini_api_key:
        logger.warning("GEMINI_API_KEY is not set; LLM responses will use a fallback message.")
        state.llm_client = None
        return
    from google import genai

    state.llm_client = genai.Client(api_key=settings.gemini_api_key)
    logger.info("Gemini client configured (model=%s)", settings.gemini_model)


def load_piper(state: ModelState, voice_dir: os.PathLike[str] | str) -> None:
    """Load optional local Piper voices for Hindi and Telugu."""
    try:
        from piper import PiperVoice
    except ImportError:
        logger.info("piper-tts not installed; Piper TTS unavailable.")
        return

    for lang, name in PIPER_VOICES.items():
        model_path = os.path.join(voice_dir, f"{name}.onnx")
        config_path = f"{model_path}.json"
        if not (os.path.exists(model_path) and os.path.exists(config_path)):
            logger.info(
                "Piper voice %s not found in %s (run scripts/download_piper_voices.sh)", name, voice_dir
            )
            continue
        try:
            state.piper_voices[lang] = PiperVoice.load(model_path, config_path)
            logger.info("Loaded Piper voice %s", name)
        except Exception:
            logger.exception("Failed to load Piper voice %s", name)


def load_models(state: ModelState, settings: Settings) -> None:
    """Load every model. Safe to run in a background thread."""
    with state.lock:
        if state.loaded or state.loading:
            return
        state.loading = True
    try:
        import torch

        state.device = "cuda" if torch.cuda.is_available() else "cpu"
        logger.info("Using device: %s", state.device)
        load_vad(state)
        load_whisper(state, settings.whisper_model)
        load_llm(state, settings)
        load_piper(state, settings.piper_voice_dir)
        state.loaded = True
        state.error = None
        logger.info("All models loaded")
    except Exception as exc:
        logger.exception("Model loading failed")
        state.error = f"{type(exc).__name__}: {exc}"
    finally:
        state.loading = False


# ============================================================================
# AUDIO HELPERS
# ============================================================================


def _decode_with_av(audio_bytes: bytes, sample_rate: int) -> np.ndarray:
    """Decode any FFmpeg-supported container (WebM/Opus, MP3, M4A...) to mono float32 via PyAV."""
    import av

    chunks: list[np.ndarray] = []
    with av.open(io.BytesIO(audio_bytes), mode="r") as container:
        stream = next((s for s in container.streams if s.type == "audio"), None)
        if stream is None:
            raise ValueError("No audio stream found")
        resampler = av.AudioResampler(format="flt", layout="mono", rate=sample_rate)
        for frame in container.decode(stream):
            for out in resampler.resample(frame):
                chunks.append(out.to_ndarray().reshape(-1))
        for out in resampler.resample(None):
            chunks.append(out.to_ndarray().reshape(-1))
    if not chunks:
        raise ValueError("No audio decoded")
    return np.concatenate(chunks).astype(np.float32)


def decode_audio(audio_bytes: bytes) -> tuple[np.ndarray, int]:
    """Decode an uploaded audio file to mono float32 samples.

    WAV/FLAC/OGG are read with libsndfile. Anything else (notably the
    `audio/webm` recordings produced by browsers' MediaRecorder) falls back to
    PyAV (installed as a faster-whisper dependency).
    """
    import soundfile as sf

    try:
        data, sample_rate = sf.read(io.BytesIO(audio_bytes), dtype="float32")
    except sf.LibsndfileError:
        return _decode_with_av(audio_bytes, TARGET_SAMPLE_RATE), TARGET_SAMPLE_RATE
    if data.ndim > 1:
        data = data.mean(axis=1)
    return data.astype(np.float32), int(sample_rate)


def float32_to_pcm16(audio: np.ndarray) -> bytes:
    return (np.clip(audio, -1.0, 1.0) * 32767).astype("<i2").tobytes()


def encoded_audio_to_pcm16(audio_bytes: bytes, sample_rate: int) -> bytes:
    """Decode MP3/WAV TTS output to raw mono PCM16 at `sample_rate` for streaming."""
    import soundfile as sf

    try:
        data, sr = sf.read(io.BytesIO(audio_bytes), dtype="float32")
        if data.ndim > 1:
            data = data.mean(axis=1)
        data = resample(data, int(sr), sample_rate)
    except sf.LibsndfileError:
        data = _decode_with_av(audio_bytes, sample_rate)
    return float32_to_pcm16(data)


def pcm16_to_float32(pcm: bytes) -> np.ndarray:
    """Convert little-endian 16-bit PCM bytes to float32 samples in [-1, 1)."""
    usable = len(pcm) - (len(pcm) % 2)
    return np.frombuffer(pcm[:usable], dtype=np.int16).astype(np.float32) / 32768.0


def resample(audio: np.ndarray, orig_sr: int, target_sr: int = TARGET_SAMPLE_RATE) -> np.ndarray:
    if orig_sr == target_sr:
        return audio
    import librosa

    return librosa.resample(audio, orig_sr=orig_sr, target_sr=target_sr)


# ============================================================================
# PIPELINE STAGES
# ============================================================================


def run_vad(state: ModelState, audio: np.ndarray, sample_rate: int) -> dict[str, Any]:
    import torch

    start = time.perf_counter()
    audio = resample(audio, sample_rate)
    tensor = torch.from_numpy(np.ascontiguousarray(audio, dtype=np.float32)).to(state.device)
    get_speech_timestamps = state.vad_utils[0]
    timestamps = get_speech_timestamps(tensor, state.vad_model, sampling_rate=TARGET_SAMPLE_RATE)
    return {
        "has_speech": len(timestamps) > 0,
        "speech_segments": timestamps,
        "latency_ms": round((time.perf_counter() - start) * 1000, 2),
    }


def run_stt(
    state: ModelState, audio: np.ndarray, sample_rate: int, language: str | None = None
) -> dict[str, Any]:
    start = time.perf_counter()
    audio16k = np.ascontiguousarray(resample(audio, sample_rate), dtype=np.float32)
    segments, info = state.whisper_model.transcribe(audio16k, language=language, beam_size=5, vad_filter=True)
    text = " ".join(seg.text for seg in segments).strip()
    return {
        "text": text,
        "detected_language": info.language,
        "confidence": round(float(info.language_probability), 3),
        "latency_ms": round((time.perf_counter() - start) * 1000, 2),
    }


def generate_llm_text(state: ModelState, system_instruction: str, user_text: str) -> str:
    """Call Gemini. Raises `LLMUnavailableError` on any failure."""
    if state.llm_client is None:
        raise LLMUnavailableError("LLM is not configured (set GEMINI_API_KEY)")
    from google.genai import types

    try:
        response = state.llm_client.models.generate_content(
            model=state.llm_model_name,
            contents=user_text,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                temperature=0.7,
                max_output_tokens=200,
            ),
        )
    except Exception as exc:
        raise LLMUnavailableError(str(exc)) from exc

    text = (response.text or "").strip()
    if not text:
        raise LLMUnavailableError("LLM returned an empty response")
    return text


async def synthesize_edge(text: str, language: str) -> dict[str, Any]:
    """Synthesize speech with Microsoft Edge neural voices (returns MP3)."""
    import edge_tts

    start = time.perf_counter()
    communicate = edge_tts.Communicate(text, EDGE_VOICES.get(language, DEFAULT_EDGE_VOICE))
    chunks: list[bytes] = []
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            chunks.append(chunk["data"])
    audio = b"".join(chunks)
    return {
        "audio": audio,
        "format": "mp3",
        "sample_rate": 24000,
        "duration_ms": round(len(audio) / 48, 2),  # rough estimate at 48 kbps
        "latency_ms": round((time.perf_counter() - start) * 1000, 2),
        "provider": "edge",
    }


def synthesize_piper(state: ModelState, text: str, language: str) -> dict[str, Any]:
    """Synthesize speech locally with Piper (Hindi/Telugu only, returns WAV)."""
    import soundfile as sf

    voice = state.piper_voices.get(language)
    if voice is None:
        raise ValueError(f"No Piper voice loaded for language {language!r}")

    start = time.perf_counter()
    parts = [chunk.audio_float_array for chunk in voice.synthesize(text)]
    if not parts:
        raise ValueError("Piper produced no audio")
    samples = np.concatenate(parts)

    sample_rate = voice.config.sample_rate
    buffer = io.BytesIO()
    sf.write(buffer, np.clip(samples, -1.0, 1.0), sample_rate, format="WAV", subtype="PCM_16")
    return {
        "audio": buffer.getvalue(),
        "format": "wav",
        "sample_rate": sample_rate,
        "duration_ms": round(len(samples) / sample_rate * 1000, 2),
        "latency_ms": round((time.perf_counter() - start) * 1000, 2),
        "provider": "piper",
    }


def to_base64(audio: bytes) -> str:
    return base64.b64encode(audio).decode("ascii")
