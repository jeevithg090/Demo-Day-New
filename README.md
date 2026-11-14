# Apollo Hospital Voice AI Assistant

A real-time, multilingual voice assistant for hospital patients. A caller speaks in
**Kannada, Tamil, Telugu, Hindi or English**, and the assistant transcribes the
question, works out what they need and how urgent it is, answers in the same
language with a spoken reply, and flags emergencies for human escalation.

Built as a hackathon demo-day project. The goal was a voice pipeline for Indian
regional languages, with hospital staff able to change the assistant's knowledge
and behaviour from an admin panel without touching code.

## Features

- **Two interaction modes**
  - *Ask*: press-and-hold to record (or upload an audio file). The full pipeline runs and
    the UI shows the transcript, the reply, the detected signals and per-stage latency.
  - *AI Call (Live)*: a WebSocket voice call. Turns are detected from silence, and replies are
    streamed back sentence by sentence as PCM audio.
- **Language handling**: Faster-Whisper STT plus Unicode-script language detection.
  Replies come back in the caller's language and script.
- **Layer-1 signals before the LLM**: keyword-based intent (medical query, appointment,
  billing/admin, greeting), urgency, stress and tone, in all five languages.
- **Safety gate with five escalation rules**: critical symptoms (plus admin-defined
  escalation keywords), chest/breathing complaints, low-confidence medical queries,
  multiple urgency cues, and the same question repeated within a session.
- **Policy engine**: the hospital name, tone, word limit, disclaimer and emergency/helpline
  numbers are injected into every LLM prompt from the admin config.
- **RAG over hospital data**: FAQs, doctors and departments are embedded with a
  multilingual sentence-transformer and searched with FAISS.
- **Session memory**: patient name, age, symptoms, duration and requested doctor are
  extracted from each turn and fed back as context.
- **Admin panel** (`/admin`): edit settings, doctors, departments, FAQs and escalation
  keywords. Writes can be protected with a bearer token.
- **Graceful degradation**: works without Redis (in-memory sessions, bundled sample data)
  and without an LLM key (a fixed fallback reply). The UI loads while models are still
  loading in the background.

## Tech stack

| Layer | Technology |
| --- | --- |
| API / server | FastAPI, Uvicorn, WebSockets |
| Voice activity detection | Silero VAD (`silero-vad`, PyTorch) |
| Speech-to-text | Faster-Whisper (`large-v3` by default) |
| LLM | Google Gemini via `google-genai` (`gemini-2.0-flash` by default) |
| Text-to-speech | Microsoft Edge neural voices (`edge-tts`); optional local Piper voices for Hindi/Telugu |
| Retrieval | sentence-transformers (`paraphrase-multilingual-MiniLM-L12-v2`) + FAISS |
| Storage | Redis (config, doctors, departments, FAQs, sessions with TTL) |
| Frontend | Vanilla HTML/CSS/JS, MediaRecorder + Web Audio API |
| Tooling | pytest, fakeredis, Ruff |

## Architecture

```
 Browser (static/)                         FastAPI (app.py)
 ─────────────────                         ─────────────────────────────────────────────
 Ask mode ── POST /process (webm/wav) ──▶  decode ─▶ VAD ─▶ STT ─▶ script language detection
                                                                   │
 Live call ── WS /ws/call (PCM16) ──────▶  energy turn detection   ▼
                                           Layer-1 signals ─▶ safety gate ─▶ policy
                                                                   │
                                          session context + RAG ─▶ Gemini LLM
                                                                   │
 ◀── JSON + base64 audio / PCM stream ───────────────── TTS (Edge or Piper)
 Admin panel ── /api/admin/* ───────────▶  Redis (config, doctors, departments, FAQs, sessions)
```

| Module | Responsibility |
| --- | --- |
| `app.py` | FastAPI app: routes, admin auth, pipeline orchestration, WebSocket call loop |
| `voice_ai/settings.py` | Environment-based configuration (reads `.env`) |
| `voice_ai/signals.py` | Pure logic: language detection, signals, safety gate, policy, prompts |
| `voice_ai/speech.py` | Model loading and VAD / STT / LLM / TTS wrappers, audio decoding |
| `voice_ai/conversation.py` | Session store and context extraction (name, age, symptoms, ...) |
| `voice_ai/embeddings.py` | Embedding model, FAISS index, RAG retrieval and prompt formatting |
| `voice_ai/redis_store.py` | Redis CRUD with graceful offline fallback |
| `voice_ai/streaming.py` | Live-call turn detection and barge-in |

## Getting started

### Prerequisites

- Python 3.10+ (developed and tested on 3.13)
- Optional: Redis 6+ for persistent admin data and sessions
- Optional: an NVIDIA GPU. `large-v3` Whisper is slow on CPU, so set `WHISPER_MODEL=small`
  or `medium` for CPU-only machines.
- Internet access on first run, to download the Whisper and embedding models from Hugging Face,
  and at runtime for Gemini and Edge TTS.

### Setup

```bash
git clone https://github.com/jeevithg090/Demo-Day-New.git
cd Demo-Day-New
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env          # then set GEMINI_API_KEY (and ADMIN_TOKEN)

# Optional: Redis + sample hospital data
redis-server --daemonize yes
python scripts/seed_data.py --clear

# Optional: local Piper voices for Hindi/Telugu (~63 MB each)
./scripts/download_piper_voices.sh
```

On macOS/Linux, `./run.sh` does all of the above (venv, install, Redis start, seed) and then
starts the server with auto-reload.

### Run

```bash
uvicorn app:app --host 0.0.0.0 --port 8000
```

| URL | What |
| --- | --- |
| http://localhost:8000 | Voice assistant UI |
| http://localhost:8000/admin | Admin panel |
| http://localhost:8000/health | Model / Redis / LLM status |
| http://localhost:8000/docs | Interactive API docs (OpenAPI) |

The server starts at once and loads models in the background. `/health` reports
`loading`, then `healthy` (or `error` with the reason). Browsers allow microphone access
only on `localhost` or over HTTPS.

## Configuration

All settings are environment variables. They can also go in a `.env` file; real environment
variables take precedence. See [`.env.example`](.env.example).

| Variable | Default | Description |
| --- | --- | --- |
| `GEMINI_API_KEY` | *(empty)* | Google Gemini API key. Without it, replies use a fixed fallback message. |
| `GEMINI_MODEL` | `gemini-2.0-flash` | Gemini model name |
| `WHISPER_MODEL` | `large-v3` | Faster-Whisper model size (`tiny` … `large-v3`) |
| `REDIS_HOST` / `REDIS_PORT` / `REDIS_DB` | `localhost` / `6379` / `0` | Redis connection |
| `ADMIN_TOKEN` | *(empty)* | Bearer token required for admin write endpoints. **Set this anywhere but local dev.** |
| `CORS_ORIGINS` | *(empty)* | Comma-separated allowed origins. Empty means same-origin only. |
| `MAX_UPLOAD_MB` | `10` | Maximum `/process` upload size |
| `PIPER_VOICE_DIR` | `./piper_voices` | Directory with Piper `.onnx` voices |
| `LOAD_MODELS` | `true` | Set to `false` to run only the web/admin/session API (no speech models) |
| `HF_TOKEN` | *(empty)* | Only for the gated models used in `experiments/` |

## API overview

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/health` | Status of models, LLM, Redis and RAG |
| `GET` | `/languages` | Supported language codes |
| `POST` | `/process` | Multipart: `audio` (WAV/WebM/MP3/...), optional `language`, `session_id`, `tts_provider` (`edge`/`piper`) |
| `WS` | `/ws/call` | Live call. Send `{"language": "hi", "sample_rate": 24000}`, then binary PCM16 mono. Receives status/transcript/latency JSON and PCM16 24 kHz audio. |
| `POST` | `/api/session` | Create a conversation session |
| `GET` | `/api/session/{id}` | Session history and extracted context |
| `GET/POST` | `/api/admin/config` | Read / update hospital configuration |
| `GET/POST/PUT/DELETE` | `/api/admin/doctors[/{id}]` | Doctor CRUD |
| `GET/POST/DELETE` | `/api/admin/departments[/{id}]` | Department CRUD |
| `GET/POST/PUT/DELETE` | `/api/admin/faqs[/{id}]` | FAQ CRUD |

When `ADMIN_TOKEN` is set, admin write requests need `Authorization: Bearer <token>`. The
admin panel asks for the token the first time a change is rejected and keeps it for the
browser tab session.

## Development

```bash
pip install -r requirements-dev.txt   # lightweight: no torch/whisper needed for the unit tests
pytest                                # unit + API tests (Redis is faked with fakeredis)
ruff check . && ruff format --check . # lint + formatting
```

`tests/test_audio_integration.py` exercises real audio decoding and Silero VAD. Those tests
are skipped automatically unless the full `requirements.txt` is installed.

## Deployment notes

- Run **one worker process** per instance (`uvicorn app:app --workers 1`). Models live in
  process memory, and the live-call state is per-connection. Scale horizontally behind a load
  balancer with sticky WebSocket connections, and use Redis so sessions are shared.
- Put the app behind HTTPS (for example a reverse proxy such as Nginx or Caddy). Browsers block
  microphone access on plain HTTP, and the proxy must forward WebSocket upgrades for `/ws/call`.
- Always set `ADMIN_TOKEN` in production. Set `CORS_ORIGINS` only if the frontend is served
  from another origin.
- A GPU host (CUDA) is recommended for `large-v3`. On CPU, use `WHISPER_MODEL=small`.
- Pre-download models at build time (start the app once, or warm the Hugging Face cache) so
  cold starts don't depend on network downloads.

## Project structure

```
.
├── app.py                    # FastAPI application and pipeline orchestration
├── voice_ai/                 # Core package (see Architecture)
├── static/                   # Frontend: index.html/app.js (assistant), admin.html/admin.js
├── data/sample_hospital.json # Sample config, doctors, departments and FAQs
├── scripts/
│   ├── seed_data.py          # Load the sample data into Redis
│   └── download_piper_voices.sh
├── tests/                    # pytest suite
├── experiments/              # Research notebook and TTS benchmark scripts (not used by the app)
├── piper_voices/             # Downloaded Piper voices (git-ignored)
├── requirements.txt          # Runtime dependencies
├── requirements-dev.txt      # Test/lint dependencies
├── pyproject.toml            # Ruff and pytest configuration
├── .env.example              # Documented configuration template
└── run.sh                    # One-shot local dev launcher
```

## Limitations

- Intent, urgency and safety detection are keyword-based. They are deliberately simple and
  explainable, but they are not a clinical triage system. **This is a demo, not a medical device.**
- Live-call turn detection uses an energy threshold. A noisy environment can trigger false turns.
- Barge-in is not effective yet. The server does not read caller audio while it is generating
  or streaming a reply, so the caller cannot interrupt mid-answer.
- Edge TTS and Gemini are external services, so their latency and availability depend on the network.
