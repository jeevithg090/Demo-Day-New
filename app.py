"""Apollo Hospital Voice AI Assistant - FastAPI backend.

Real-time regional-language voice assistant for patient queries in Kannada,
Tamil, Telugu, Hindi and English.

Pipeline: VAD -> STT -> language detection -> Layer-1 signals -> safety gate
-> policy -> LLM (with RAG + session memory) -> TTS.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import secrets
import threading
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any

from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from voice_ai import speech
from voice_ai.conversation import ConversationManager
from voice_ai.embeddings import RAGRetriever, load_documents_from_json, load_documents_from_redis
from voice_ai.redis_store import RedisStore, get_redis
from voice_ai.settings import BASE_DIR, Settings, settings
from voice_ai.signals import (
    SUPPORTED_LANGUAGES,
    apply_policy,
    build_call_system_prompt,
    build_system_prompt,
    detect_script_language,
    extract_layer1_signals,
    run_safety_gate,
    split_sentences,
)
from voice_ai.streaming import StreamingSession

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("apollo")

STATIC_DIR = BASE_DIR / "static"
SAMPLE_DATA_PATH = BASE_DIR / "data" / "sample_hospital.json"
CALL_OUTPUT_SAMPLE_RATE = 24000  # the browser plays raw PCM16 at this rate
WS_AUDIO_CHUNK_BYTES = 4096
SESSION_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")

# ============================================================================
# REQUEST MODELS
# ============================================================================


class ConfigUpdate(BaseModel):
    hospital_name: str | None = None
    city: str | None = None
    emergency_number: str | None = None
    helpline: str | None = None
    tone: str | None = None
    max_words: int | None = Field(default=None, ge=5, le=500)
    disclaimer_text: str | None = None
    disclaimer_required: bool | None = None
    always_recommend_doctor: bool | None = None
    tts_enabled: bool | None = None
    session_timeout_minutes: int | None = Field(default=None, ge=1, le=24 * 60)
    escalation_keywords: list[str] | None = None
    supported_languages: list[str] | None = None


class DoctorCreate(BaseModel):
    id: str | None = None
    name: str = Field(min_length=1)
    specialization: str
    department: str
    available_days: list[str]
    timings: str
    languages: list[str] = ["en"]
    consultation_fee: str | None = None
    name_kn: str | None = None
    name_hi: str | None = None
    name_ta: str | None = None
    name_te: str | None = None


class DepartmentCreate(BaseModel):
    id: str | None = None
    name: str = Field(min_length=1)
    floor: str
    timings: str
    contact: str | None = None
    name_kn: str | None = None


class FAQCreate(BaseModel):
    id: str | None = None
    question: str = Field(min_length=1)
    answer: str = Field(min_length=1)
    category: str | None = "general"
    question_kn: str | None = None
    question_hi: str | None = None
    answer_kn: str | None = None


# ============================================================================
# APPLICATION STATE
# ============================================================================


class AppServices:
    """Long-lived services shared by all requests."""

    def __init__(self, cfg: Settings, store: RedisStore | None = None):
        self.settings = cfg
        self.models = speech.ModelState()
        self.redis: RedisStore = store or get_redis()
        self.conversations = ConversationManager(self.redis)
        self.rag = RAGRetriever()

    def hospital_config(self) -> dict[str, Any]:
        return self.redis.get_config()

    def load_knowledge_base(self) -> list[dict[str, Any]]:
        if self.redis.is_connected:
            return load_documents_from_redis(self.redis)
        return load_documents_from_json(str(SAMPLE_DATA_PATH))

    def rebuild_rag_index(self) -> None:
        """(Re)build the RAG index from the current knowledge base."""
        docs = self.load_knowledge_base()
        self.rag.documents = []
        self.rag.add_documents(docs)
        if not docs:
            return
        try:
            self.rag.build_index()
        except Exception:
            logger.exception("RAG index build failed; RAG features disabled")


services: AppServices | None = None


def get_services() -> AppServices:
    if services is None:
        raise HTTPException(status_code=503, detail="Service is starting up")
    return services


def init_services(cfg: Settings = settings, store: RedisStore | None = None) -> AppServices:
    global services
    services = AppServices(cfg, store)
    return services


def _background_startup(svc: AppServices) -> None:
    svc.rebuild_rag_index()
    speech.load_models(svc.models, svc.settings)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    svc = services or init_services()
    logger.info("Starting Apollo Hospital Voice AI Assistant (redis=%s)", svc.redis.is_connected)
    if svc.settings.load_models:
        # Load models in the background so the UI and /health respond immediately.
        threading.Thread(target=_background_startup, args=(svc,), daemon=True).start()
    else:
        logger.warning("LOAD_MODELS=false: speech pipeline disabled")
    yield
    logger.info("Shutting down")


app = FastAPI(
    title="Apollo Hospital Voice AI Assistant",
    description="Real-time regional-language voice AI for patient health queries",
    version="2.1.0",
    lifespan=lifespan,
)

if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# ============================================================================
# AUTH
# ============================================================================


def require_admin(
    authorization: str | None = Header(default=None),
    svc: AppServices = Depends(get_services),
) -> None:
    """Protect admin write endpoints with a bearer token when ADMIN_TOKEN is set."""
    expected = svc.settings.admin_token
    if not expected:
        return
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not secrets.compare_digest(token.strip(), expected):
        raise HTTPException(
            status_code=401,
            detail="Admin token required",
            headers={"WWW-Authenticate": "Bearer"},
        )


def require_redis(svc: AppServices = Depends(get_services)) -> RedisStore:
    if not svc.redis.is_connected:
        raise HTTPException(status_code=503, detail="Redis not connected")
    return svc.redis


# ============================================================================
# PAGES & HEALTH
# ============================================================================


@app.get("/", include_in_schema=False)
async def root():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/admin", include_in_schema=False)
async def admin_page():
    return FileResponse(STATIC_DIR / "admin.html")


@app.get("/health")
async def health_check(svc: AppServices = Depends(get_services)):
    m = svc.models
    return {
        "status": m.status,
        "models_loaded": m.loaded,
        "model_error": m.error,
        "device": m.device,
        "llm_configured": svc.settings.llm_configured,
        "piper_languages": sorted(m.piper_voices),
        "redis_connected": svc.redis.is_connected,
        "rag_ready": svc.rag.is_ready,
        "admin_auth_enabled": bool(svc.settings.admin_token),
        "supported_languages": SUPPORTED_LANGUAGES,
    }


@app.get("/languages")
async def get_languages():
    return {"languages": SUPPORTED_LANGUAGES}


# ============================================================================
# ADMIN API
# ============================================================================

admin_write = [Depends(require_admin)]


@app.get("/api/admin/config")
async def get_config(svc: AppServices = Depends(get_services)):
    return svc.hospital_config()


@app.post("/api/admin/config", dependencies=admin_write)
async def update_config(config: ConfigUpdate, store: RedisStore = Depends(require_redis)):
    current = store.get_config()
    current.update(config.model_dump(exclude_none=True))
    if not store.set_config(current):
        raise HTTPException(status_code=500, detail="Failed to update config")
    return {"success": True, "config": store.get_config()}


@app.get("/api/admin/doctors")
async def get_doctors(svc: AppServices = Depends(get_services)):
    return {"doctors": svc.redis.get_doctors()}


@app.post("/api/admin/doctors", dependencies=admin_write)
async def add_doctor(
    doctor: DoctorCreate,
    store: RedisStore = Depends(require_redis),
    svc: AppServices = Depends(get_services),
):
    doc_id = store.add_doctor(doctor.model_dump(exclude_none=True, exclude={"id"}))
    if not doc_id:
        raise HTTPException(status_code=500, detail="Failed to add doctor")
    await run_in_threadpool(svc.rebuild_rag_index)
    return {"success": True, "id": doc_id}


@app.put("/api/admin/doctors/{doc_id}", dependencies=admin_write)
async def update_doctor(
    doc_id: str,
    doctor: DoctorCreate,
    store: RedisStore = Depends(require_redis),
    svc: AppServices = Depends(get_services),
):
    if not store.update_doctor(doc_id, doctor.model_dump(exclude_none=True, exclude={"id"})):
        raise HTTPException(status_code=404, detail="Doctor not found")
    await run_in_threadpool(svc.rebuild_rag_index)
    return {"success": True}


@app.delete("/api/admin/doctors/{doc_id}", dependencies=admin_write)
async def delete_doctor(
    doc_id: str,
    store: RedisStore = Depends(require_redis),
    svc: AppServices = Depends(get_services),
):
    if not store.delete_doctor(doc_id):
        raise HTTPException(status_code=404, detail="Doctor not found")
    await run_in_threadpool(svc.rebuild_rag_index)
    return {"success": True}


@app.get("/api/admin/departments")
async def get_departments(svc: AppServices = Depends(get_services)):
    return {"departments": svc.redis.get_departments()}


@app.post("/api/admin/departments", dependencies=admin_write)
async def add_department(
    department: DepartmentCreate,
    store: RedisStore = Depends(require_redis),
    svc: AppServices = Depends(get_services),
):
    dept_id = store.add_department(department.model_dump(exclude_none=True, exclude={"id"}))
    if not dept_id:
        raise HTTPException(status_code=500, detail="Failed to add department")
    await run_in_threadpool(svc.rebuild_rag_index)
    return {"success": True, "id": dept_id}


@app.delete("/api/admin/departments/{dept_id}", dependencies=admin_write)
async def delete_department(
    dept_id: str,
    store: RedisStore = Depends(require_redis),
    svc: AppServices = Depends(get_services),
):
    if not store.delete_department(dept_id):
        raise HTTPException(status_code=404, detail="Department not found")
    await run_in_threadpool(svc.rebuild_rag_index)
    return {"success": True}


@app.get("/api/admin/faqs")
async def get_faqs(svc: AppServices = Depends(get_services)):
    return {"faqs": svc.redis.get_faqs()}


@app.post("/api/admin/faqs", dependencies=admin_write)
async def add_faq(
    faq: FAQCreate,
    store: RedisStore = Depends(require_redis),
    svc: AppServices = Depends(get_services),
):
    faq_id = store.add_faq(faq.model_dump(exclude_none=True, exclude={"id"}))
    if not faq_id:
        raise HTTPException(status_code=500, detail="Failed to add FAQ")
    await run_in_threadpool(svc.rebuild_rag_index)
    return {"success": True, "id": faq_id}


@app.put("/api/admin/faqs/{faq_id}", dependencies=admin_write)
async def update_faq(
    faq_id: str,
    faq: FAQCreate,
    store: RedisStore = Depends(require_redis),
    svc: AppServices = Depends(get_services),
):
    if not store.update_faq(faq_id, faq.model_dump(exclude_none=True, exclude={"id"})):
        raise HTTPException(status_code=404, detail="FAQ not found")
    await run_in_threadpool(svc.rebuild_rag_index)
    return {"success": True}


@app.delete("/api/admin/faqs/{faq_id}", dependencies=admin_write)
async def delete_faq(
    faq_id: str,
    store: RedisStore = Depends(require_redis),
    svc: AppServices = Depends(get_services),
):
    if not store.delete_faq(faq_id):
        raise HTTPException(status_code=404, detail="FAQ not found")
    await run_in_threadpool(svc.rebuild_rag_index)
    return {"success": True}


# ============================================================================
# SESSION API
# ============================================================================


@app.post("/api/session")
async def create_session(svc: AppServices = Depends(get_services)):
    return {"session_id": svc.conversations.create_session()}


@app.get("/api/session/{session_id}")
async def get_session(session_id: str, svc: AppServices = Depends(get_services)):
    session = svc.conversations.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


# ============================================================================
# PIPELINE
# ============================================================================


def _llm_respond(svc: AppServices, system_instruction: str, text: str) -> str:
    try:
        return speech.generate_llm_text(svc.models, system_instruction, text)
    except speech.LLMUnavailableError as exc:
        logger.warning("LLM unavailable: %s", exc)
        return speech.LLM_FALLBACK_RESPONSE


def analyze_and_respond(svc: AppServices, text: str, language: str, session_id: str | None) -> dict[str, Any]:
    """Run signals -> safety -> policy -> LLM for a transcript. Blocking."""
    signals = extract_layer1_signals(text, language)
    repeat_count = svc.conversations.get_repeated_query_count(session_id, text) if session_id else 0
    config = svc.hospital_config()
    safety = run_safety_gate(text, language, signals, repeat_count, config.get("escalation_keywords"))
    policy = apply_policy(safety, language, config)

    context_string = ""
    if session_id:
        context_string = svc.conversations.get_conversation_context(session_id)["context_string"]

    rag_context = ""
    if svc.rag.is_ready:
        results = svc.rag.search(text, top_k=2, language=language)
        rag_context = svc.rag.format_results_for_prompt(results)

    llm_start = time.perf_counter()
    system_instruction = build_system_prompt(language, signals, safety, policy, context_string, rag_context)
    response_text = _llm_respond(svc, system_instruction, text)
    llm_latency = round((time.perf_counter() - llm_start) * 1000, 2)

    return {
        "signals": signals,
        "safety": safety,
        "policy": policy,
        "response": response_text,
        "used_rag": bool(rag_context),
        "used_context": bool(context_string) and not context_string.startswith("New conversation"),
        "llm_latency_ms": llm_latency,
    }


async def synthesize(svc: AppServices, text: str, language: str, provider: str) -> dict[str, Any]:
    if provider == "piper" and language in svc.models.piper_voices:
        try:
            return await run_in_threadpool(speech.synthesize_piper, svc.models, text, language)
        except Exception:
            logger.exception("Piper TTS failed; falling back to Edge TTS")
    return await speech.synthesize_edge(text, language)


@app.post("/process")
async def process_audio(
    audio: UploadFile = File(...),
    language: str | None = Form(None),
    session_id: str | None = Form(None),
    tts_provider: str = Form("edge"),
    svc: AppServices = Depends(get_services),
):
    """Run the full voice pipeline on an uploaded recording."""
    if not svc.models.loaded:
        raise HTTPException(status_code=503, detail="Models are still loading")
    if language and language not in SUPPORTED_LANGUAGES:
        raise HTTPException(status_code=400, detail=f"Unsupported language: {language}")
    if tts_provider not in {"edge", "piper"}:
        raise HTTPException(status_code=400, detail="tts_provider must be 'edge' or 'piper'")

    total_start = time.perf_counter()
    audio_bytes = await audio.read(svc.settings.max_upload_bytes + 1)
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="Empty audio upload")
    if len(audio_bytes) > svc.settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail="Audio file too large")

    if not session_id or not SESSION_ID_RE.fullmatch(session_id):
        session_id = svc.conversations.create_session()
    elif not svc.conversations.get_session(session_id):
        svc.conversations.create_session(session_id)

    try:
        audio_data, sample_rate = await run_in_threadpool(speech.decode_audio, audio_bytes)
    except Exception:
        logger.warning("Could not decode uploaded audio", exc_info=True)
        raise HTTPException(status_code=400, detail="Unsupported or corrupt audio file") from None

    try:
        vad = await run_in_threadpool(speech.run_vad, svc.models, audio_data, sample_rate)
        if not vad["has_speech"]:
            return JSONResponse(
                {
                    "success": False,
                    "error": "No speech detected in audio",
                    "session_id": session_id,
                    "pipeline": {"vad": {"latency_ms": vad["latency_ms"]}},
                }
            )

        stt = await run_in_threadpool(speech.run_stt, svc.models, audio_data, sample_rate, language)
        if not stt["text"]:
            return JSONResponse(
                {
                    "success": False,
                    "error": "Could not transcribe audio",
                    "session_id": session_id,
                    "pipeline": {"vad": {"latency_ms": vad["latency_ms"]}, "stt": stt},
                }
            )

        lang_result = detect_script_language(stt["text"])
        detected_lang = language or lang_result["language"]

        result = await run_in_threadpool(analyze_and_respond, svc, stt["text"], detected_lang, session_id)
        tts = await synthesize(svc, result["response"], detected_lang, tts_provider)

        svc.conversations.add_turn(session_id, stt["text"], result["response"], detected_lang)
    except Exception:
        logger.exception("Error processing audio")
        raise HTTPException(status_code=500, detail="Internal error while processing audio") from None

    signals, safety, policy = result["signals"], result["safety"], result["policy"]
    return {
        "success": True,
        "session_id": session_id,
        "transcription": stt["text"],
        "response": result["response"],
        "audio_response": speech.to_base64(tts["audio"]),
        "audio_format": tts["format"],
        "audio_sample_rate": tts["sample_rate"],
        "tts_provider": tts["provider"],
        "language": detected_lang,
        "language_name": SUPPORTED_LANGUAGES.get(detected_lang, "Unknown"),
        "escalation": {
            "should_escalate": safety["should_escalate"],
            "reason": safety["escalation_reason"],
            "rules_triggered": safety["rules_triggered"],
        },
        "context": {"used_rag": result["used_rag"], "used_history": result["used_context"]},
        "pipeline": {
            "vad": {"latency_ms": vad["latency_ms"]},
            "stt": {"latency_ms": stt["latency_ms"], "confidence": stt["confidence"]},
            "language_detection": {"latency_ms": lang_result["latency_ms"]},
            "layer1_signals": {
                "latency_ms": signals["latency_ms"],
                "intent": signals["intent"],
                "urgency": signals["urgency"],
                "tone": signals["tone"],
                "stress_level": signals["stress_level"],
            },
            "safety_gate": {"latency_ms": safety["latency_ms"]},
            "policy": {"latency_ms": policy["latency_ms"]},
            "llm": {"latency_ms": result["llm_latency_ms"]},
            "tts": {"latency_ms": tts["latency_ms"], "duration_ms": tts["duration_ms"]},
        },
        "total_latency_ms": round((time.perf_counter() - total_start) * 1000, 2),
    }


# ============================================================================
# LIVE CALL (WEBSOCKET)
# ============================================================================


def _call_turn(svc: AppServices, session_id: str, pcm: bytes, sample_rate: int, language: str):
    """STT + LLM for one live-call turn. Blocking; returns (transcript, sentences)."""
    audio = speech.resample(speech.pcm16_to_float32(pcm), sample_rate)
    text = speech.run_stt(svc.models, audio, speech.TARGET_SAMPLE_RATE, language=language)["text"]
    if not text:
        return "", []

    signals = extract_layer1_signals(text, language)
    repeat_count = svc.conversations.get_repeated_query_count(session_id, text)
    config = svc.hospital_config()
    safety = run_safety_gate(text, language, signals, repeat_count, config.get("escalation_keywords"))
    policy = apply_policy(safety, language, config)
    context_string = svc.conversations.get_conversation_context(session_id)["context_string"]

    system_instruction = build_call_system_prompt(language, signals, safety, policy, context_string)
    response_text = _llm_respond(svc, system_instruction, text)
    svc.conversations.add_turn(session_id, text, response_text, language)
    return text, split_sentences(response_text)


async def _sentence_to_pcm(svc: AppServices, sentence: str, language: str) -> bytes:
    provider = "piper" if language in svc.models.piper_voices else "edge"
    tts = await synthesize(svc, sentence, language, provider)
    return await run_in_threadpool(speech.encoded_audio_to_pcm16, tts["audio"], CALL_OUTPUT_SAMPLE_RATE)


@app.websocket("/ws/call")
async def websocket_call(websocket: WebSocket):
    svc = services
    await websocket.accept()
    if svc is None or not svc.models.loaded:
        await websocket.send_json({"type": "error", "message": "Models are still loading"})
        await websocket.close(code=1013)
        return

    session_id = uuid.uuid4().hex[:8]
    svc.conversations.create_session(session_id)
    stream = StreamingSession(session_id)

    try:
        config = json.loads(await websocket.receive_text())
        language = config.get("language") or "en"
        if language not in SUPPORTED_LANGUAGES:
            language = "en"
        try:
            input_rate = int(config.get("sample_rate") or speech.TARGET_SAMPLE_RATE)
        except (TypeError, ValueError):
            input_rate = speech.TARGET_SAMPLE_RATE
        stream.language = language
        logger.info("Call started: %s [%s, %d Hz]", session_id, language, input_rate)

        while True:
            data = await websocket.receive_bytes()
            event = stream.process_incoming_audio(data)
            if not event:
                continue

            if event["type"] == "interrupt":
                await websocket.send_json({"type": "status", "state": "listening"})
                continue

            # turn_complete
            await websocket.send_json({"type": "status", "state": "thinking"})
            turn_start = time.perf_counter()
            try:
                text, sentences = await run_in_threadpool(
                    _call_turn, svc, session_id, event["audio"], input_rate, language
                )
                if text:
                    await websocket.send_json({"type": "transcript", "text": text, "role": "user"})
                    stream.state = "speaking"
                    await websocket.send_json({"type": "status", "state": "speaking"})
                    first_audio = True
                    for sentence in sentences:
                        pcm = await _sentence_to_pcm(svc, sentence, language)
                        if first_audio:
                            latency = round((time.perf_counter() - turn_start) * 1000)
                            await websocket.send_json({"type": "latency", "value": latency})
                            first_audio = False
                        for i in range(0, len(pcm), WS_AUDIO_CHUNK_BYTES):
                            await websocket.send_bytes(pcm[i : i + WS_AUDIO_CHUNK_BYTES])
                            await asyncio.sleep(0)
            except WebSocketDisconnect:
                raise
            except Exception:
                logger.exception("Error during call turn")
                await websocket.send_json({"type": "error", "message": "Failed to generate a response"})
            stream.state = "listening"
            stream.reset_for_new_turn()
            await websocket.send_json({"type": "status", "state": "listening"})

    except WebSocketDisconnect:
        logger.info("Call ended: %s", session_id)
    except Exception:
        logger.exception("WebSocket error")
        with contextlib.suppress(RuntimeError):
            await websocket.close(code=1011)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
