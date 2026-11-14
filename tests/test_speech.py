import io
import sys
import types

import numpy as np
import pytest
import soundfile as sf

import app as app_module
from voice_ai import speech
from voice_ai.settings import Settings


def test_pcm_roundtrip():
    samples = np.array([0.0, 0.5, -0.5, 0.999], dtype=np.float32)
    pcm = speech.float32_to_pcm16(samples)
    assert len(pcm) == 8
    back = speech.pcm16_to_float32(pcm)
    assert np.allclose(back, samples, atol=1e-3)
    assert speech.pcm16_to_float32(pcm + b"\x00").shape == (4,)  # odd byte ignored


def test_float32_to_pcm16_clips():
    pcm = speech.float32_to_pcm16(np.array([2.0, -2.0], dtype=np.float32))
    assert list(np.frombuffer(pcm, dtype="<i2")) == [32767, -32767]


def test_decode_audio_wav_stereo_to_mono():
    stereo = np.stack([np.full(800, 0.2), np.full(800, 0.4)], axis=1)
    buf = io.BytesIO()
    sf.write(buf, stereo, 8000, format="WAV")
    data, rate = speech.decode_audio(buf.getvalue())
    assert rate == 8000
    assert data.ndim == 1 and data.dtype == np.float32
    assert np.allclose(data, 0.3, atol=1e-3)


def test_resample_noop_without_librosa():
    audio = np.zeros(10, dtype=np.float32)
    assert speech.resample(audio, 16000) is audio


def test_llm_requires_api_key():
    with pytest.raises(speech.LLMUnavailableError):
        speech.generate_llm_text(speech.ModelState(), "system", "hi")


def test_load_llm_without_key_leaves_client_unset():
    state = speech.ModelState()
    speech.load_llm(state, Settings(gemini_api_key=""))
    assert state.llm_client is None
    assert state.llm_model_name == "gemini-2.0-flash"


class FakeModels:
    def __init__(self, reply="Please visit the OPD."):
        self.reply = reply
        self.calls = []

    def generate_content(self, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        return types.SimpleNamespace(text=self.reply)


@pytest.fixture
def fake_genai(monkeypatch):
    """Stub `google.genai.types` so the LLM path runs without the SDK or network."""
    genai_types = types.ModuleType("google.genai.types")
    genai_types.GenerateContentConfig = lambda **kw: kw
    genai = types.ModuleType("google.genai")
    genai.types = genai_types
    google = sys.modules.get("google") or types.ModuleType("google")
    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setattr(google, "genai", genai, raising=False)
    monkeypatch.setitem(sys.modules, "google.genai", genai)
    monkeypatch.setitem(sys.modules, "google.genai.types", genai_types)


def make_services(store, fake_models=None):
    svc = app_module.AppServices(Settings(load_models=False), store)
    if fake_models is not None:
        svc.models.llm_client = types.SimpleNamespace(models=fake_models)
        svc.models.llm_model_name = "test-model"
    return svc


def test_analyze_and_respond_uses_session_context(fake_genai, redis_store):
    fake = FakeModels()
    svc = make_services(redis_store, fake)
    sid = svc.conversations.create_session()
    svc.conversations.add_turn(sid, "My name is Asha", "Hello Asha", "en")

    result = app_module.analyze_and_respond(svc, "I have chest pain", "en", sid)

    assert result["response"] == "Please visit the OPD."
    assert result["safety"]["should_escalate"] is True
    assert result["policy"]["priority"] == "urgent"
    assert result["used_context"] is True
    system_instruction = fake.calls[0]["config"]["system_instruction"]
    assert "Name: Asha" in system_instruction
    assert "URGENT" in system_instruction
    assert fake.calls[0]["model"] == "test-model"


def test_analyze_and_respond_falls_back_without_llm(redis_store):
    svc = make_services(redis_store)
    result = app_module.analyze_and_respond(svc, "What are visiting hours?", "en", None)
    assert result["response"] == speech.LLM_FALLBACK_RESPONSE
    assert result["safety"]["should_escalate"] is False


def test_repeated_questions_escalate(fake_genai, redis_store):
    svc = make_services(redis_store, FakeModels())
    sid = svc.conversations.create_session()
    for _ in range(2):
        svc.conversations.add_turn(sid, "where is the pharmacy", "Ground floor", "en")
    result = app_module.analyze_and_respond(svc, "where is the pharmacy", "en", sid)
    assert "rule_5_repeated_query" in result["safety"]["rules_triggered"]
