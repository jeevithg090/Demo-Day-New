/**
 * Apollo Voice AI Assistant: frontend for the Ask and Live call modes.
 *
 * Ask:       hold-to-record (pointer or keyboard) or upload a file, POST /process,
 *            render the transcript, reply, pipeline latencies and signals.
 * Live call: WebSocket /ws/call streaming PCM16 both ways.
 */

const LANGUAGE_NAMES = { en: 'English', kn: 'Kannada', ta: 'Tamil', te: 'Telugu', hi: 'Hindi' };
const PIPER_LANGUAGES = ['en', 'hi', 'te'];
const PIPELINE_STAGES = ['vad', 'stt', 'language_detection', 'layer1_signals', 'safety_gate', 'policy', 'llm', 'tts'];
const MIN_RECORDING_MS = 400;
const SESSION_KEY = 'apollo_session_id';

const state = {
    isRecording: false,
    isProcessing: false,
    serverReady: false,
    mediaRecorder: null,
    audioChunks: [],
    recordStartedAt: 0,
    stopRequested: false,
    sessionId: null,
    maxUploadBytes: 10 * 1024 * 1024,
    stageTimers: [],
};

const $ = (id) => document.getElementById(id);

const el = {
    status: $('statusIndicator'),
    notice: $('serviceNotice'),
    noticeTitle: $('serviceNoticeTitle'),
    noticeText: $('serviceNoticeText'),
    languageSelect: $('languageSelect'),
    ttsToggle: $('ttsToggle'),
    micButton: $('micButton'),
    micLabel: $('micLabel'),
    micHint: $('micHint'),
    uploadButton: $('uploadButton'),
    audioUpload: $('audioUpload'),
    fileName: $('fileName'),
    newSessionBtn: $('newSessionBtn'),
    totalLatency: document.querySelector('#totalLatency .latency-value'),
    escalationAlert: $('escalationAlert'),
    escalationReason: $('escalationReason'),
    emergencyNumber: $('emergencyNumber'),
    emergencyCall: $('emergencyCall'),
    conversation: $('conversationContainer'),
    audioPlayer: $('audioPlayerSection'),
    responseAudio: $('responseAudio'),
    signals: $('signalsSection'),
    signalIntent: $('signalIntent'),
    signalUrgency: $('signalUrgency'),
    signalTone: $('signalTone'),
    signalLanguage: $('signalLanguage'),
    contextBadges: $('contextIndicators'),
};

const toast = (message, type) => window.ui.toast(message, type);

// ============================================================================
// SERVER STATUS
// ============================================================================

function setStatus(kind, text) {
    el.status.dataset.state = kind;
    el.status.querySelector('.status-text').textContent = text;
}

function showNotice(title, text, tone = 'warning') {
    el.notice.className = `alert alert-${tone}`;
    el.noticeTitle.textContent = title;
    el.noticeText.textContent = text;
    el.notice.hidden = false;
}

function setInputsEnabled(enabled) {
    el.micButton.disabled = !enabled;
    el.uploadButton.disabled = !enabled;
    if (callManager) callManager.setAvailable(enabled);
}

async function checkServerHealth() {
    let data;
    try {
        const response = await fetch('/health', { cache: 'no-store' });
        data = await response.json();
    } catch {
        state.serverReady = false;
        setInputsEnabled(false);
        setStatus('error', 'Offline');
        setMicHint('Cannot reach the server. Retrying…');
        showNotice('Server unavailable', 'The assistant server is not responding. This page will reconnect automatically.', 'danger');
        setTimeout(checkServerHealth, 5000);
        return;
    }

    if (data.max_upload_mb) state.maxUploadBytes = data.max_upload_mb * 1024 * 1024;
    if (el.fileName && !el.fileName.dataset.file) {
        el.fileName.textContent = `WAV, WebM, MP3 or OGG · up to ${data.max_upload_mb || 10} MB`;
    }

    if (data.status === 'healthy') {
        const wasReady = state.serverReady;
        state.serverReady = true;
        setInputsEnabled(true);
        setStatus('ready', 'Ready');
        setMicHint(defaultMicHint());
        if (!data.llm_configured) {
            showNotice(
                'Running without an LLM key',
                'GEMINI_API_KEY is not set, so replies use a fixed fallback message. Speech recognition and signals still work.',
                'info',
            );
        } else {
            el.notice.hidden = true;
        }
        if (!wasReady && data.piper_languages && data.piper_languages.length === 0) {
            el.ttsToggle.disabled = true;
            $('ttsHint').textContent = 'Piper voices not installed (see README)';
        }
        return;
    }

    state.serverReady = false;
    setInputsEnabled(false);

    if (data.status === 'error') {
        setStatus('error', 'Models failed');
        setMicHint('Speech is unavailable.');
        showNotice('Speech models failed to load', 'Check the server logs. The admin panel still works.', 'danger');
        return;
    }

    if (data.speech_enabled === false) {
        setStatus('disabled', 'Speech off');
        setMicHint('Speech is turned off on this server.');
        showNotice(
            'Speech pipeline is turned off',
            'The server was started with LOAD_MODELS=false, so recording and calls are unavailable. The admin panel and APIs still work.',
            'info',
        );
        return;
    }

    setStatus('loading', 'Loading models…');
    setMicHint('Loading speech models…');
    showNotice(
        'Speech models are loading',
        'The first start downloads models and can take a few minutes. This page updates automatically.',
    );
    setTimeout(checkServerHealth, 3000);
}

async function loadEmergencyNumber() {
    try {
        const response = await fetch('/api/admin/config');
        if (!response.ok) return;
        const config = await response.json();
        const number = String(config.emergency_number || '').trim();
        if (number) {
            el.emergencyNumber.textContent = number;
            el.emergencyCall.href = `tel:${number.replace(/[^\d+]/g, '')}`;
            document.querySelectorAll('.disclaimer strong').forEach((n) => (n.textContent = number));
        }
    } catch {
        /* keep the default 108 */
    }
}

// ============================================================================
// SESSION
// ============================================================================

function storage(action, value) {
    try {
        if (action === 'get') return sessionStorage.getItem(SESSION_KEY);
        if (action === 'set') sessionStorage.setItem(SESSION_KEY, value);
        if (action === 'remove') sessionStorage.removeItem(SESSION_KEY);
    } catch {
        /* storage unavailable: the session lives only in memory */
    }
    return null;
}

async function initSession() {
    const stored = storage('get');
    if (stored) {
        try {
            const response = await fetch(`/api/session/${encodeURIComponent(stored)}`);
            if (response.ok) {
                state.sessionId = stored;
                restoreHistory(await response.json());
                el.newSessionBtn.disabled = false;
                return;
            }
        } catch {
            /* fall through and create a new session */
        }
    }
    await createSession();
}

async function createSession() {
    try {
        const response = await fetch('/api/session', { method: 'POST' });
        const data = await response.json();
        state.sessionId = data.session_id;
        storage('set', state.sessionId);
    } catch {
        state.sessionId = null; // /process creates one on the first request
    }
    el.newSessionBtn.disabled = false;
}

function restoreHistory(session) {
    let history = session && session.history;
    if (typeof history === 'string') {
        try {
            history = JSON.parse(history);
        } catch {
            history = [];
        }
    }
    if (!Array.isArray(history) || history.length === 0) return;
    const language = LANGUAGE_NAMES[session.language] || '';
    history.forEach((turn) => addMessage(turn.role === 'user' ? 'user' : 'assistant', turn.text, language));
}

async function startNewConversation() {
    storage('remove');
    state.sessionId = null;
    el.conversation.innerHTML = emptyStateHtml();
    el.audioPlayer.hidden = true;
    el.signals.hidden = true;
    el.contextBadges.hidden = true;
    hideEscalation();
    resetPipeline();
    await createSession();
    toast('Started a new conversation', 'success');
}

function emptyStateHtml() {
    return `
        <div class="state-block empty-state">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
            <h3>No messages yet</h3>
            <p>Hold the microphone and ask something like “What are the OPD timings?” or “I have had a fever for two days.”</p>
        </div>`;
}

// ============================================================================
// RECORDING (press and hold: pointer, touch or Space/Enter)
// ============================================================================

function defaultMicHint() {
    return 'Hold to talk, release to send. Keyboard: hold Space.';
}

function setMicHint(text, tone = '') {
    el.micHint.textContent = text;
    el.micHint.dataset.tone = tone;
}

function pickMimeType() {
    if (typeof MediaRecorder === 'undefined' || !MediaRecorder.isTypeSupported) return '';
    const candidates = ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus', 'audio/mp4'];
    return candidates.find((t) => MediaRecorder.isTypeSupported(t)) || '';
}

function extensionFor(mime) {
    if (mime.includes('ogg')) return 'ogg';
    if (mime.includes('mp4')) return 'm4a';
    return 'webm';
}

async function startRecording() {
    if (state.isRecording || state.isProcessing || !state.serverReady) return;
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        toast('Recording needs a secure page (HTTPS or localhost) and a browser with microphone support.', 'error');
        return;
    }

    state.isRecording = true;
    state.stopRequested = false;
    el.micButton.classList.add('recording');
    el.micButton.setAttribute('aria-pressed', 'true');
    setMicHint('Recording… release to send', 'recording');

    let stream;
    try {
        stream = await navigator.mediaDevices.getUserMedia({
            audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
        });
    } catch (error) {
        resetRecordingUi();
        const denied = error && (error.name === 'NotAllowedError' || error.name === 'SecurityError');
        const message = denied
            ? 'Microphone access was blocked. Allow it in your browser’s site settings and try again.'
            : 'No microphone was found. Connect one, or upload an audio file instead.';
        setMicHint(message);
        toast(message, 'error');
        return;
    }

    const mimeType = pickMimeType();
    const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
    state.mediaRecorder = recorder;
    state.audioChunks = [];
    state.recordStartedAt = performance.now();

    recorder.ondataavailable = (event) => {
        if (event.data.size > 0) state.audioChunks.push(event.data);
    };
    recorder.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop());
        const duration = performance.now() - state.recordStartedAt;
        const type = recorder.mimeType || mimeType || 'audio/webm';
        if (duration < MIN_RECORDING_MS || state.audioChunks.length === 0) {
            setMicHint('That was too short. Hold the button while you speak.');
            return;
        }
        const blob = new Blob(state.audioChunks, { type });
        await processAudio(blob, `recording.${extensionFor(type)}`);
    };
    recorder.start(100);

    // The user may have released before the permission prompt resolved.
    if (state.stopRequested) stopRecording();
}

function stopRecording() {
    if (!state.isRecording) return;
    if (!state.mediaRecorder) {
        state.stopRequested = true;
        return;
    }
    const recorder = state.mediaRecorder;
    state.mediaRecorder = null;
    resetRecordingUi();
    if (recorder.state !== 'inactive') recorder.stop();
}

function resetRecordingUi() {
    state.isRecording = false;
    el.micButton.classList.remove('recording');
    el.micButton.setAttribute('aria-pressed', 'false');
    if (!state.isProcessing) setMicHint(defaultMicHint());
}

function setupMicButton() {
    const mic = el.micButton;
    mic.setAttribute('aria-pressed', 'false');

    mic.addEventListener('pointerdown', (e) => {
        if (e.button !== 0) return;
        e.preventDefault();
        mic.setPointerCapture?.(e.pointerId);
        startRecording();
    });
    ['pointerup', 'pointercancel'].forEach((type) => mic.addEventListener(type, stopRecording));
    mic.addEventListener('contextmenu', (e) => e.preventDefault());

    mic.addEventListener('keydown', (e) => {
        if ((e.key === ' ' || e.key === 'Enter') && !e.repeat) {
            e.preventDefault();
            startRecording();
        }
    });
    mic.addEventListener('keyup', (e) => {
        if (e.key === ' ' || e.key === 'Enter') {
            e.preventDefault();
            stopRecording();
        }
    });
    mic.addEventListener('blur', stopRecording);
}

// ============================================================================
// UPLOAD
// ============================================================================

async function handleFileUpload(event) {
    const file = event.target.files[0];
    event.target.value = '';
    if (!file) return;

    if (file.type && !file.type.startsWith('audio/') && file.type !== 'video/webm') {
        toast(`“${file.name}” is not an audio file.`, 'error');
        return;
    }
    if (file.size > state.maxUploadBytes) {
        const mb = Math.round(state.maxUploadBytes / 1024 / 1024);
        toast(`That file is ${(file.size / 1024 / 1024).toFixed(1)} MB. The limit is ${mb} MB.`, 'error');
        return;
    }

    el.fileName.textContent = file.name;
    el.fileName.dataset.file = '1';
    await processAudio(file, file.name);
}

// ============================================================================
// PROCESSING
// ============================================================================

function friendlyError(status, data) {
    const detail = data && (data.error || (typeof data.detail === 'string' ? data.detail : ''));
    if (detail === 'No speech detected in audio') return 'No speech was detected. Speak a little louder or closer to the microphone.';
    if (detail === 'Could not transcribe audio') return 'Sorry, the audio could not be understood. Please try again.';
    if (status === 413) return 'That recording is too large to upload.';
    if (status === 503) return 'The speech models are still loading. Try again in a moment.';
    if (status === 429) return 'Too many requests. Please wait a moment.';
    if (status >= 500) return 'Something went wrong on the server. Please try again.';
    return detail || 'Processing failed. Please try again.';
}

async function processAudio(blob, filename) {
    if (state.isProcessing) return;
    state.isProcessing = true;
    setStatus('processing', 'Processing…');
    setMicHint('Working on your question…', 'processing');
    el.micButton.classList.add('processing');
    el.micButton.disabled = true;
    el.uploadButton.disabled = true;
    resetPipeline();
    hideEscalation();
    animatePipeline();

    const form = new FormData();
    form.append('audio', blob, filename);
    form.append('language', el.languageSelect.value);
    form.append('tts_provider', el.ttsToggle.checked ? 'piper' : 'edge');
    if (state.sessionId) form.append('session_id', state.sessionId);

    try {
        const response = await fetch('/process', { method: 'POST', body: form });
        let data = null;
        try {
            data = await response.json();
        } catch {
            /* non-JSON error page */
        }

        if (response.ok && data && data.success) {
            if (data.session_id && data.session_id !== state.sessionId) {
                state.sessionId = data.session_id;
                storage('set', state.sessionId);
            }
            renderResult(data);
        } else {
            if (data && data.session_id) {
                state.sessionId = data.session_id;
                storage('set', state.sessionId);
            }
            resetPipeline(data && data.pipeline);
            const message = friendlyError(response.status, data);
            toast(message, 'error');
            setMicHint(message);
        }
    } catch {
        resetPipeline();
        const message = 'Could not reach the server. Check your connection and try again.';
        toast(message, 'error');
        setMicHint(message);
    } finally {
        state.isProcessing = false;
        el.micButton.classList.remove('processing');
        if (state.serverReady) {
            setStatus('ready', 'Ready');
            el.micButton.disabled = false;
            el.uploadButton.disabled = false;
        }
        if (el.micHint.dataset.tone === 'processing') setMicHint(defaultMicHint());
    }
}

function stageEl(name) {
    return document.querySelector(`.stage[data-stage="${name}"]`);
}

function animatePipeline() {
    PIPELINE_STAGES.forEach((name, i) => {
        state.stageTimers.push(setTimeout(() => stageEl(name)?.classList.add('processing'), i * 220));
    });
}

function resetPipeline(partial) {
    state.stageTimers.forEach(clearTimeout);
    state.stageTimers = [];
    PIPELINE_STAGES.forEach((name) => {
        const node = stageEl(name);
        if (!node) return;
        node.classList.remove('processing', 'complete', 'skipped');
        const done = partial && partial[name];
        if (done) node.classList.add('complete');
        node.querySelector('.stage-latency').textContent = done ? `${Math.round(done.latency_ms)} ms` : '--';
    });
    el.totalLatency.textContent = '--';
}

function renderResult(data) {
    state.stageTimers.forEach(clearTimeout);
    state.stageTimers = [];
    PIPELINE_STAGES.forEach((name) => {
        const node = stageEl(name);
        const stage = data.pipeline && data.pipeline[name];
        node.classList.remove('processing');
        node.classList.add(stage ? 'complete' : 'skipped');
        node.querySelector('.stage-latency').textContent = stage ? `${Math.round(stage.latency_ms)} ms` : 'skipped';
    });
    el.totalLatency.textContent = `${Math.round(data.total_latency_ms)} ms`;

    if (data.escalation && data.escalation.should_escalate) showEscalation(data.escalation.reason);

    addMessage('user', data.transcription, data.language_name);
    addMessage('assistant', data.response, data.language_name);

    if (data.audio_response) playAudio(data.audio_response, data.audio_format);
    if (data.pipeline && data.pipeline.layer1_signals) renderSignals(data.pipeline.layer1_signals, data.language_name);
    renderContext(data.context);
}

function addMessage(role, text, language) {
    el.conversation.querySelector('.empty-state')?.remove();

    const wrap = document.createElement('div');
    wrap.className = `message ${role}`;

    const meta = document.createElement('div');
    meta.className = 'message-meta';
    const who = document.createElement('span');
    who.textContent = role === 'user' ? 'You' : 'Apollo AI';
    meta.appendChild(who);
    if (language) {
        const lang = document.createElement('span');
        lang.className = 'message-language';
        lang.textContent = language;
        meta.appendChild(lang);
    }

    const body = document.createElement('p');
    body.className = 'message-text';
    body.textContent = text;

    wrap.append(meta, body);
    el.conversation.appendChild(wrap);
    el.conversation.scrollTop = el.conversation.scrollHeight;
}

const AUDIO_MIME_TYPES = { mp3: 'audio/mpeg', wav: 'audio/wav' };
let currentAudioUrl = null;

function playAudio(base64Audio, format = 'mp3') {
    const bytes = Uint8Array.from(atob(base64Audio), (c) => c.charCodeAt(0));
    const blob = new Blob([bytes], { type: AUDIO_MIME_TYPES[format] || 'audio/mpeg' });
    if (currentAudioUrl) URL.revokeObjectURL(currentAudioUrl);
    currentAudioUrl = URL.createObjectURL(blob);
    el.responseAudio.src = currentAudioUrl;
    el.audioPlayer.hidden = false;
    el.responseAudio.play().catch(() => {
        /* autoplay blocked: the player stays visible for manual playback */
    });
}

function renderSignals(signals, language) {
    el.signals.hidden = false;
    const set = (node, value) => {
        node.textContent = String(value || '--').replace(/_/g, ' ');
        node.className = `signal-value ${value || ''}`;
    };
    set(el.signalIntent, signals.intent);
    set(el.signalUrgency, signals.urgency);
    set(el.signalTone, signals.tone);
    el.signalLanguage.textContent = language || '--';
}

function renderContext(context) {
    el.contextBadges.replaceChildren();
    if (!context) return;
    const badges = [];
    if (context.used_rag) badges.push(['badge badge-primary', 'Answered from hospital FAQs']);
    if (context.used_history) badges.push(['badge badge-success', 'Used earlier conversation']);
    badges.forEach(([cls, text]) => {
        const b = document.createElement('span');
        b.className = cls;
        b.textContent = text;
        el.contextBadges.appendChild(b);
    });
    el.contextBadges.hidden = badges.length === 0;
}

function showEscalation(reason) {
    el.escalationReason.textContent = reason || 'Your message mentions symptoms that may need urgent care.';
    el.escalationAlert.hidden = false;
    el.escalationAlert.classList.add('shake');
    setTimeout(() => el.escalationAlert.classList.remove('shake'), 500);
}

function hideEscalation() {
    el.escalationAlert.hidden = true;
}

// ============================================================================
// SETTINGS
// ============================================================================

function handleTtsToggle() {
    const piper = el.ttsToggle.checked;
    Array.from(el.languageSelect.options).forEach((option) => {
        const unsupported = piper && !PIPER_LANGUAGES.includes(option.value);
        option.disabled = unsupported;
    });
    if (piper && !PIPER_LANGUAGES.includes(el.languageSelect.value)) {
        el.languageSelect.value = 'en';
        toast('The fast local voice supports English, Hindi and Telugu. Switched to English.', 'info');
    }
}

// ============================================================================
// TABS (WAI-ARIA tabs pattern: arrow keys, Home/End)
// ============================================================================

function initTabs() {
    const tabs = Array.from(document.querySelectorAll('[role="tab"]'));

    function select(tab, focus = true) {
        tabs.forEach((t) => {
            const active = t === tab;
            t.setAttribute('aria-selected', String(active));
            t.tabIndex = active ? 0 : -1;
            $(t.getAttribute('aria-controls')).hidden = !active;
        });
        if (focus) tab.focus();
        if (tab.dataset.tab === 'call') callManager?.resizeCanvas();
    }

    tabs.forEach((tab, i) => {
        tab.addEventListener('click', () => select(tab, false));
        tab.addEventListener('keydown', (e) => {
            let next = null;
            if (e.key === 'ArrowRight') next = tabs[(i + 1) % tabs.length];
            if (e.key === 'ArrowLeft') next = tabs[(i - 1 + tabs.length) % tabs.length];
            if (e.key === 'Home') next = tabs[0];
            if (e.key === 'End') next = tabs[tabs.length - 1];
            if (next) {
                e.preventDefault();
                select(next);
            }
        });
    });
}

// ============================================================================
// LIVE CALL (WebSocket + Web Audio)
// ============================================================================

class CallManager {
    constructor() {
        this.socket = null;
        this.audioContext = null;
        this.processor = null;
        this.mediaStream = null;
        this.nextStartTime = 0;
        this.available = false;
        this.active = false;
        this.endedByUser = false;

        this.startBtn = $('startCallBtn');
        this.endBtn = $('endCallBtn');
        this.statusText = $('callStatusText');
        this.hint = $('callHint');
        this.latencyText = $('callLatencyText');
        this.avatar = document.querySelector('.call-avatar');
        this.languageSelect = $('callLanguageSelect');
        this.canvas = $('audioVisualizer');
        this.ctx = this.canvas.getContext('2d');

        this.startBtn.addEventListener('click', () => this.startCall());
        this.endBtn.addEventListener('click', () => this.endCall(true));
        window.addEventListener('resize', () => this.resizeCanvas());
        document.addEventListener('themechange', () => this.drawIdle());
        this.resizeCanvas();
        this.setState('idle');
    }

    setAvailable(available) {
        this.available = available;
        if (!this.active) {
            this.startBtn.disabled = !available;
            if (!available) this.hint.textContent = 'Live calls are available once the speech models are ready.';
            else if (this.statusText.dataset.tone === 'neutral') this.hint.textContent = 'Talk naturally. The assistant replies when you pause.';
        }
    }

    setState(kind, message) {
        const labels = {
            idle: 'Ready to call',
            connecting: 'Connecting…',
            listening: 'Listening…',
            thinking: 'Thinking…',
            speaking: 'Apollo is speaking',
            error: 'Call unavailable',
        };
        const tones = { idle: 'neutral', connecting: 'neutral', listening: 'listening', thinking: 'thinking', speaking: 'speaking', error: 'error' };
        this.statusText.textContent = labels[kind] || kind;
        this.statusText.dataset.tone = tones[kind] || 'neutral';
        this.avatar.dataset.state = kind;
        if (message !== undefined) this.hint.textContent = message;

        this.active = !['idle', 'error'].includes(kind);
        this.startBtn.disabled = this.active || !this.available;
        this.endBtn.disabled = !this.active;
        this.languageSelect.disabled = this.active;
        if (!this.active) {
            this.latencyText.textContent = 'Latency: -- ms';
            this.drawIdle();
        }
    }

    async startCall() {
        if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
            this.setState('error', 'Calls need a secure page (HTTPS or localhost) and microphone support.');
            return;
        }
        this.endedByUser = false;
        this.setState('connecting', 'Allow microphone access if your browser asks.');

        try {
            this.mediaStream = await navigator.mediaDevices.getUserMedia({
                audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 },
            });
        } catch (e) {
            const denied = e && (e.name === 'NotAllowedError' || e.name === 'SecurityError');
            this.setState(
                'error',
                denied
                    ? 'Microphone access was blocked. Allow it in your browser’s site settings, then try again.'
                    : 'No microphone was found. Connect one and try again.',
            );
            return;
        }

        const Ctx = window.AudioContext || window.webkitAudioContext;
        this.audioContext = new Ctx({ sampleRate: 24000, latencyHint: 'interactive' });

        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        this.socket = new WebSocket(`${protocol}//${window.location.host}/ws/call`);
        this.socket.binaryType = 'arraybuffer';

        this.socket.onopen = () => {
            this.socket.send(
                JSON.stringify({ type: 'config', language: this.languageSelect.value, sample_rate: this.audioContext.sampleRate }),
            );
            this.startStreaming();
        };
        this.socket.onmessage = (event) => {
            if (typeof event.data === 'string') this.handleSignal(JSON.parse(event.data));
            else this.playChunk(event.data);
        };
        this.socket.onerror = () => {
            this.lastError = 'The connection to the server failed.';
        };
        this.socket.onclose = () => {
            const message = this.endedByUser ? 'Call ended.' : this.lastError || 'The call was disconnected.';
            this.cleanup();
            if (this.endedByUser) this.setState('idle', message);
            else this.setState('error', message);
            this.lastError = null;
        };
    }

    startStreaming() {
        const source = this.audioContext.createMediaStreamSource(this.mediaStream);
        // ScriptProcessor keeps this dependency-free; AudioWorklet would need a separate module file.
        this.processor = this.audioContext.createScriptProcessor(4096, 1, 1);
        this.processor.onaudioprocess = (e) => {
            if (!this.socket || this.socket.readyState !== WebSocket.OPEN) return;
            const input = e.inputBuffer.getChannelData(0);
            this.socket.send(floatTo16BitPCM(input));
            this.draw(input);
        };
        source.connect(this.processor);
        this.processor.connect(this.audioContext.destination);
        this.setState('listening', 'Talk naturally. The assistant replies when you pause.');
    }

    handleSignal(msg) {
        if (msg.type === 'status' && ['listening', 'thinking', 'speaking'].includes(msg.state)) {
            this.setState(msg.state);
        } else if (msg.type === 'latency') {
            this.latencyText.textContent = `Latency: ${msg.value} ms`;
        } else if (msg.type === 'transcript' && msg.text) {
            this.hint.textContent = `You said: “${msg.text}”`;
        } else if (msg.type === 'error') {
            this.lastError = msg.message || 'Something went wrong.';
            this.hint.textContent = this.lastError;
        }
    }

    playChunk(arrayBuffer) {
        if (!this.audioContext) return;
        const samples = int16ToFloat32(arrayBuffer);
        const buffer = this.audioContext.createBuffer(1, samples.length, 24000);
        buffer.getChannelData(0).set(samples);
        const source = this.audioContext.createBufferSource();
        source.buffer = buffer;
        source.connect(this.audioContext.destination);
        const now = this.audioContext.currentTime;
        if (this.nextStartTime < now) this.nextStartTime = now + 0.05;
        source.start(this.nextStartTime);
        this.nextStartTime += buffer.duration;
    }

    endCall(byUser = false) {
        this.endedByUser = byUser;
        if (this.socket && this.socket.readyState <= WebSocket.OPEN) {
            this.socket.close();
        } else {
            this.cleanup();
            this.setState('idle', 'Call ended.');
        }
    }

    cleanup() {
        this.socket = null;
        if (this.processor) {
            this.processor.disconnect();
            this.processor.onaudioprocess = null;
            this.processor = null;
        }
        if (this.mediaStream) {
            this.mediaStream.getTracks().forEach((t) => t.stop());
            this.mediaStream = null;
        }
        if (this.audioContext) {
            this.audioContext.close().catch(() => {});
            this.audioContext = null;
        }
        this.nextStartTime = 0;
    }

    // --- visualiser ---------------------------------------------------------

    resizeCanvas() {
        const rect = this.canvas.getBoundingClientRect();
        if (!rect.width) return;
        const dpr = window.devicePixelRatio || 1;
        this.canvas.width = Math.round(rect.width * dpr);
        this.canvas.height = Math.round(rect.height * dpr);
        this.drawIdle();
    }

    colors() {
        const css = getComputedStyle(document.documentElement);
        return { bg: css.getPropertyValue('--surface-2').trim(), line: css.getPropertyValue('--primary-text').trim() };
    }

    drawIdle() {
        this.draw(null);
    }

    draw(data) {
        const { width, height } = this.canvas;
        if (!width) return;
        const { bg, line } = this.colors();
        const ctx = this.ctx;
        ctx.fillStyle = bg;
        ctx.fillRect(0, 0, width, height);
        ctx.lineWidth = 2 * (window.devicePixelRatio || 1);
        ctx.strokeStyle = line;
        ctx.beginPath();
        if (!data) {
            ctx.globalAlpha = 0.4;
            ctx.moveTo(0, height / 2);
            ctx.lineTo(width, height / 2);
            ctx.stroke();
            ctx.globalAlpha = 1;
            return;
        }
        const step = Math.max(1, Math.floor(data.length / width));
        for (let i = 0, x = 0; i < data.length; i += step, x += width / (data.length / step)) {
            const y = (0.5 + Math.max(-1, Math.min(1, data[i] * 8)) / 2) * height;
            if (i === 0) ctx.moveTo(x, y);
            else ctx.lineTo(x, y);
        }
        ctx.stroke();
    }
}

function floatTo16BitPCM(input) {
    const output = new Int16Array(input.length);
    for (let i = 0; i < input.length; i++) {
        const s = Math.max(-1, Math.min(1, input[i]));
        output[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
    }
    return output.buffer;
}

function int16ToFloat32(buffer) {
    const int16 = new Int16Array(buffer);
    const out = new Float32Array(int16.length);
    for (let i = 0; i < int16.length; i++) out[i] = int16[i] / 32768;
    return out;
}

// ============================================================================
// START
// ============================================================================

let callManager = null;

document.addEventListener('DOMContentLoaded', () => {
    callManager = new CallManager();
    initTabs();
    setupMicButton();
    el.uploadButton.addEventListener('click', () => el.audioUpload.click());
    el.audioUpload.addEventListener('change', handleFileUpload);
    el.newSessionBtn.addEventListener('click', startNewConversation);
    el.ttsToggle.addEventListener('change', handleTtsToggle);

    checkServerHealth();
    loadEmergencyNumber();
    initSession();
});
