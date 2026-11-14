/**
 * Apollo Hospital Voice AI Assistant v2.0
 * Frontend JavaScript for audio recording, processing, and UI updates
 * 
 * v2.0 Features:
 * - Session management with session_id
 * - Context display showing patient info
 * - RAG/context indicators
 */

// ============================================================================
// STATE
// ============================================================================

const state = {
    isRecording: false,
    mediaRecorder: null,
    audioChunks: [],
    isProcessing: false,
    serverReady: false,
    sessionId: null
};

// ============================================================================
// DOM ELEMENTS
// ============================================================================

const elements = {
    statusIndicator: document.getElementById('statusIndicator'),
    languageSelect: document.getElementById('languageSelect'),
    ttsToggle: document.getElementById('ttsToggle'),
    micButton: document.getElementById('micButton'),
    recordingIndicator: document.getElementById('recordingIndicator'),
    uploadButton: document.getElementById('uploadButton'),
    audioUpload: document.getElementById('audioUpload'),
    fileName: document.getElementById('fileName'),
    pipelineSection: document.getElementById('pipelineSection'),
    totalLatency: document.getElementById('totalLatency'),
    escalationAlert: document.getElementById('escalationAlert'),
    escalationReason: document.getElementById('escalationReason'),
    conversationSection: document.getElementById('conversationSection'),
    conversationContainer: document.getElementById('conversationContainer'),
    audioPlayerSection: document.getElementById('audioPlayerSection'),
    responseAudio: document.getElementById('responseAudio'),
    signalsSection: document.getElementById('signalsSection'),
    signalIntent: document.getElementById('signalIntent'),
    signalUrgency: document.getElementById('signalUrgency'),
    signalTone: document.getElementById('signalTone'),
    signalLanguage: document.getElementById('signalLanguage'),
    // New elements for session/context (may not exist in old HTML)
    sessionInfo: document.getElementById('sessionInfo'),
    contextInfo: document.getElementById('contextInfo')
};

// ============================================================================
// INITIALIZATION
// ============================================================================

async function init() {
    // Check server health
    await checkServerHealth();

    // Set up event listeners
    setupEventListeners();

    // Check for microphone permissions
    await checkMicrophonePermission();

    // Initialize or restore session
    await initSession();
}

async function checkServerHealth() {
    try {
        const response = await fetch('/health');
        const data = await response.json();

        if (data.status === 'healthy') {
            state.serverReady = true;
            updateStatus('ready', 'Ready');

            // Show additional info
            console.log('Server status:', data);
            console.log('Redis connected:', data.redis_connected);
            console.log('RAG ready:', data.rag_ready);
        } else if (data.status === 'error') {
            updateStatus('error', 'Model loading failed - check server logs');
        } else {
            updateStatus('loading', 'Loading models...');
            // Poll until ready
            setTimeout(checkServerHealth, 3000);
        }
    } catch (error) {
        updateStatus('error', 'Server unavailable');
        setTimeout(checkServerHealth, 5000);
    }
}

function updateStatus(status, text) {
    const indicator = elements.statusIndicator;
    if (indicator) {
        indicator.className = `status-indicator ${status}`;
        const statusText = indicator.querySelector('.status-text');
        if (statusText) {
            statusText.textContent = text;
        }
    }
}

async function checkMicrophonePermission() {
    try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        stream.getTracks().forEach(track => track.stop());
    } catch (error) {
        console.warn('Microphone permission not granted:', error);
    }
}

// ============================================================================
// SESSION MANAGEMENT
// ============================================================================

async function initSession() {
    // Check if we have a session in sessionStorage
    const storedSessionId = sessionStorage.getItem('apollo_session_id');

    if (storedSessionId) {
        // Validate the session still exists on server
        try {
            const response = await fetch(`/api/session/${storedSessionId}`);
            if (response.ok) {
                state.sessionId = storedSessionId;
                console.log('Restored session:', state.sessionId);
                updateSessionDisplay();
                return;
            }
        } catch (error) {
            console.log('Session expired, creating new one');
        }
    }

    // Create new session
    await createNewSession();
}

async function createNewSession() {
    try {
        const response = await fetch('/api/session', { method: 'POST' });
        const data = await response.json();

        state.sessionId = data.session_id;
        sessionStorage.setItem('apollo_session_id', state.sessionId);

        console.log('Created new session:', state.sessionId);
        updateSessionDisplay();
    } catch (error) {
        console.error('Failed to create session:', error);
        // Generate a local session ID as fallback
        state.sessionId = 'local_' + Math.random().toString(36).substring(2, 10);
    }
}

function updateSessionDisplay() {
    // Update session info display if element exists
    if (elements.sessionInfo) {
        elements.sessionInfo.textContent = `Session: ${state.sessionId}`;
    }
}

function clearSession() {
    sessionStorage.removeItem('apollo_session_id');
    state.sessionId = null;
    createNewSession();

    // Clear conversation display
    if (elements.conversationContainer) {
        elements.conversationContainer.innerHTML = `
            <div class="empty-state">
                <p>Start speaking to begin the conversation</p>
            </div>
        `;
    }

    // Hide context
    if (elements.contextInfo) {
        elements.contextInfo.hidden = true;
    }
}

// ============================================================================
// EVENT LISTENERS
// ============================================================================

function setupEventListeners() {
    // Microphone button - press and hold
    if (elements.micButton) {
        elements.micButton.addEventListener('mousedown', startRecording);
        elements.micButton.addEventListener('mouseup', stopRecording);
        elements.micButton.addEventListener('mouseleave', stopRecording);

        // Touch events for mobile
        elements.micButton.addEventListener('touchstart', (e) => {
            e.preventDefault();
            startRecording();
        });
        elements.micButton.addEventListener('touchend', (e) => {
            e.preventDefault();
            stopRecording();
        });
    }

    // Upload button
    if (elements.uploadButton) {
        elements.uploadButton.addEventListener('click', () => {
            elements.audioUpload.click();
        });
    }

    // File input change
    if (elements.audioUpload) {
        elements.audioUpload.addEventListener('change', handleFileUpload);
    }

    const newSessionBtn = document.getElementById('newSessionBtn');
    if (newSessionBtn) {
        newSessionBtn.addEventListener('click', clearSession);
    }

    // TTS Toggle
    if (elements.ttsToggle) {
        elements.ttsToggle.addEventListener('change', handleTTSToggle);
    }
}

function handleTTSToggle(e) {
    const isPiper = e.target.checked;
    const select = elements.languageSelect;

    // Supported languages for Piper: English, Hindi, Telugu
    const piperLangs = ['en', 'hi', 'te'];

    // Iterate options and hide/show based on Piper support
    for (let i = 0; i < select.options.length; i++) {
        const option = select.options[i];
        if (isPiper) {
            if (!piperLangs.includes(option.value) && option.value !== '') {
                option.hidden = true;
                option.disabled = true;
            }
        } else {
            option.hidden = false;
            option.disabled = false;
        }
    }

    // If currently selected language is not supported, switch to English
    if (isPiper && !piperLangs.includes(select.value)) {
        select.value = 'en';
    }
}

// ============================================================================
// RECORDING
// ============================================================================

async function startRecording() {
    if (state.isRecording || state.isProcessing || !state.serverReady) return;

    try {
        const stream = await navigator.mediaDevices.getUserMedia({
            audio: {
                sampleRate: 16000,
                channelCount: 1,
                echoCancellation: true,
                noiseSuppression: true
            }
        });

        state.mediaRecorder = new MediaRecorder(stream, {
            mimeType: 'audio/webm;codecs=opus'
        });

        state.audioChunks = [];

        state.mediaRecorder.ondataavailable = (event) => {
            if (event.data.size > 0) {
                state.audioChunks.push(event.data);
            }
        };

        state.mediaRecorder.onstop = async () => {
            stream.getTracks().forEach(track => track.stop());

            if (state.audioChunks.length > 0) {
                const audioBlob = new Blob(state.audioChunks, { type: 'audio/webm' });
                await processAudio(audioBlob);
            }
        };

        state.mediaRecorder.start(100);
        state.isRecording = true;

        // Update UI
        if (elements.micButton) {
            elements.micButton.classList.add('recording');
        }
        if (elements.recordingIndicator) {
            elements.recordingIndicator.classList.add('active');
        }

    } catch (error) {
        console.error('Error starting recording:', error);
        showError('Microphone access denied. Please allow microphone access.');
    }
}

function stopRecording() {
    if (!state.isRecording || !state.mediaRecorder) return;

    state.isRecording = false;
    state.mediaRecorder.stop();

    // Update UI
    if (elements.micButton) {
        elements.micButton.classList.remove('recording');
    }
    if (elements.recordingIndicator) {
        elements.recordingIndicator.classList.remove('active');
    }
}

// ============================================================================
// FILE UPLOAD
// ============================================================================

async function handleFileUpload(event) {
    const file = event.target.files[0];
    if (!file) return;

    if (elements.fileName) {
        elements.fileName.textContent = file.name;
    }
    await processAudio(file);

    // Reset file input
    event.target.value = '';
}

// ============================================================================
// AUDIO PROCESSING
// ============================================================================

async function processAudio(audioBlob) {
    if (state.isProcessing) return;

    state.isProcessing = true;
    updateStatus('processing', 'Processing...');

    // Reset pipeline stages
    resetPipelineStages();
    hideEscalationAlert();

    try {
        const formData = new FormData();
        formData.append('audio', audioBlob, 'recording.webm');

        // Add language if selected
        const selectedLanguage = elements.languageSelect ? elements.languageSelect.value : '';
        if (selectedLanguage) {
            formData.append('language', selectedLanguage);
        }

        // Add TTS provider
        const ttsProvider = elements.ttsToggle && elements.ttsToggle.checked ? 'piper' : 'edge';
        formData.append('tts_provider', ttsProvider);

        // Add session ID
        if (state.sessionId) {
            formData.append('session_id', state.sessionId);
        }

        // Show pipeline section
        if (elements.pipelineSection) {
            elements.pipelineSection.classList.add('active');
        }

        // Simulate pipeline progress
        animatePipelineProgress();

        const response = await fetch('/process', {
            method: 'POST',
            body: formData
        });

        const data = await response.json();

        if (data.success) {
            // Update session ID if server created one
            if (data.session_id && data.session_id !== state.sessionId) {
                state.sessionId = data.session_id;
                sessionStorage.setItem('apollo_session_id', state.sessionId);
                updateSessionDisplay();
            }

            handleSuccessResponse(data);
        } else {
            showError(data.error || (typeof data.detail === 'string' ? data.detail : null) || 'Processing failed');
        }

    } catch (error) {
        console.error('Error processing audio:', error);
        showError('Failed to process audio. Please try again.');
    } finally {
        state.isProcessing = false;
        updateStatus('ready', 'Ready');
    }
}

function animatePipelineProgress() {
    const stages = ['vad', 'stt', 'language_detection', 'layer1_signals', 'safety_gate', 'policy', 'llm', 'tts'];

    stages.forEach((stage, index) => {
        setTimeout(() => {
            const stageEl = document.querySelector(`[data-stage="${stage}"]`);
            if (stageEl) {
                stageEl.classList.add('processing');
            }
        }, index * 200);
    });
}

function resetPipelineStages() {
    document.querySelectorAll('.stage').forEach(stage => {
        stage.classList.remove('processing', 'complete', 'error');
        const latencyEl = stage.querySelector('.stage-latency');
        if (latencyEl) {
            latencyEl.textContent = '--';
        }
    });

    if (elements.totalLatency) {
        const latencyValue = elements.totalLatency.querySelector('.latency-value');
        if (latencyValue) {
            latencyValue.textContent = '--';
        }
    }
}

// ============================================================================
// RESPONSE HANDLING
// ============================================================================

function handleSuccessResponse(data) {
    // Update pipeline stages with actual latencies
    updatePipelineLatencies(data.pipeline);

    // Update total latency
    if (elements.totalLatency) {
        const latencyValue = elements.totalLatency.querySelector('.latency-value');
        if (latencyValue) {
            latencyValue.textContent = `${data.total_latency_ms.toFixed(0)}ms`;
        }
    }

    // Check for escalation
    if (data.escalation && data.escalation.should_escalate) {
        showEscalationAlert(data.escalation.reason);
    }

    // Add to conversation
    addToConversation('user', data.transcription, data.language_name);
    addToConversation('assistant', data.response, data.language_name);

    // Play audio response
    if (data.audio_response) {
        playAudioResponse(data.audio_response, data.audio_format);
    }

    // Update signals
    if (data.pipeline && data.pipeline.layer1_signals) {
        updateSignals(data.pipeline.layer1_signals, data.language_name);
    }

    // Show context indicators
    showContextIndicators(data.context);
}

function updatePipelineLatencies(pipeline) {
    const stages = ['vad', 'stt', 'language_detection', 'layer1_signals', 'safety_gate', 'policy', 'llm', 'tts'];

    stages.forEach(stage => {
        const stageEl = document.querySelector(`[data-stage="${stage}"]`);
        if (stageEl && pipeline[stage]) {
            stageEl.classList.remove('processing');
            stageEl.classList.add('complete');

            const latencyEl = stageEl.querySelector('.stage-latency');
            if (latencyEl) {
                latencyEl.textContent = `${pipeline[stage].latency_ms.toFixed(0)}ms`;
            }
        }
    });
}

function addToConversation(role, text, language) {
    if (!elements.conversationContainer) return;

    // Remove empty state if present
    const emptyState = elements.conversationContainer.querySelector('.empty-state');
    if (emptyState) {
        emptyState.remove();
    }

    const messageDiv = document.createElement('div');
    messageDiv.className = `message ${role}`;

    const labelSpan = document.createElement('span');
    labelSpan.className = 'message-label';
    labelSpan.textContent = role === 'user' ? 'You' : 'Apollo AI';

    const langSpan = document.createElement('span');
    langSpan.className = 'message-language';
    langSpan.textContent = language;

    const textP = document.createElement('p');
    textP.className = 'message-text';
    textP.textContent = text;

    messageDiv.appendChild(labelSpan);
    messageDiv.appendChild(langSpan);
    messageDiv.appendChild(textP);

    elements.conversationContainer.appendChild(messageDiv);
    elements.conversationContainer.scrollTop = elements.conversationContainer.scrollHeight;
}

const AUDIO_MIME_TYPES = { mp3: 'audio/mpeg', wav: 'audio/wav' };

function playAudioResponse(base64Audio, format = 'mp3') {
    const audioBlob = base64ToBlob(base64Audio, AUDIO_MIME_TYPES[format] || 'audio/mpeg');
    const audioUrl = URL.createObjectURL(audioBlob);

    if (elements.responseAudio) {
        elements.responseAudio.src = audioUrl;
        if (elements.audioPlayerSection) {
            elements.audioPlayerSection.hidden = false;
        }
        elements.responseAudio.play().catch((err) => {
            // Autoplay can be blocked by the browser; the player stays visible for manual playback.
            console.warn('Audio autoplay blocked:', err);
        });
    }
}

function base64ToBlob(base64, mimeType) {
    const byteCharacters = atob(base64);
    const byteNumbers = new Array(byteCharacters.length);

    for (let i = 0; i < byteCharacters.length; i++) {
        byteNumbers[i] = byteCharacters.charCodeAt(i);
    }

    const byteArray = new Uint8Array(byteNumbers);
    return new Blob([byteArray], { type: mimeType });
}

function updateSignals(signals, language) {
    if (elements.signalsSection) {
        elements.signalsSection.hidden = false;
    }

    if (elements.signalIntent) {
        elements.signalIntent.textContent = signals.intent;
        elements.signalIntent.className = `signal-value ${signals.intent}`;
    }

    if (elements.signalUrgency) {
        elements.signalUrgency.textContent = signals.urgency;
        elements.signalUrgency.className = `signal-value ${signals.urgency}`;
    }

    if (elements.signalTone) {
        elements.signalTone.textContent = signals.tone;
        elements.signalTone.className = `signal-value ${signals.tone}`;
    }

    if (elements.signalLanguage) {
        elements.signalLanguage.textContent = language;
    }

    // Show stress level if available
    const stressEl = document.getElementById('signalStress');
    if (stressEl && signals.stress_level) {
        stressEl.textContent = signals.stress_level;
        stressEl.className = `signal-value ${signals.stress_level}`;
    }
}

function showContextIndicators(context) {
    if (!context) return;

    // Show indicators for RAG and history usage
    const contextIndicators = document.getElementById('contextIndicators');
    if (contextIndicators) {
        let html = '';

        if (context.used_rag) {
            html += '<span class="context-badge rag">FAQ Matched</span>';
        }

        if (context.used_history) {
            html += '<span class="context-badge history">Context Used</span>';
        }

        contextIndicators.innerHTML = html;
        contextIndicators.hidden = !html;
    }
}

// ============================================================================
// ESCALATION HANDLING
// ============================================================================

function showEscalationAlert(reason) {
    if (elements.escalationAlert) {
        elements.escalationAlert.hidden = false;

        if (elements.escalationReason) {
            elements.escalationReason.textContent = reason;
        }

        // Add shake animation
        elements.escalationAlert.classList.add('shake');
        setTimeout(() => {
            elements.escalationAlert.classList.remove('shake');
        }, 500);
    }
}

function hideEscalationAlert() {
    if (elements.escalationAlert) {
        elements.escalationAlert.hidden = true;
    }
}

// ============================================================================
// ERROR HANDLING
// ============================================================================

function showError(message) {
    // Create error toast
    const toast = document.createElement('div');
    toast.className = 'error-toast';
    toast.textContent = message;

    document.body.appendChild(toast);

    setTimeout(() => {
        toast.classList.add('show');
    }, 10);

    setTimeout(() => {
        toast.classList.remove('show');
        setTimeout(() => toast.remove(), 300);
    }, 5000);
}

// ============================================================================
// START
// ============================================================================

document.addEventListener('DOMContentLoaded', () => {
    init();
    // Initialize Tabs
    initTabs();
});


// ============================================================================
// TABS & NAVIGATION
// ============================================================================

function initTabs() {
    const tabs = document.querySelectorAll('.nav-tab');

    tabs.forEach(tab => {
        tab.addEventListener('click', () => {
            // Deactivate all
            document.querySelectorAll('.nav-tab').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));

            // Activate clicked
            tab.classList.add('active');
            const targetId = tab.getAttribute('data-tab');
            const targetContent = document.getElementById(`${targetId}-tab`);
            if (targetContent) {
                targetContent.classList.add('active');
            }

            // Special handling for AI Call tab
            if (targetId === 'aicall') {
                // Resize visualizer if needed
                if (callManager && callManager.visualizer) {
                    callManager.visualizer.resize();
                }
            }
        });
    });
}

// ============================================================================
// CALL MANAGER (WebSockets + Streaming)
// ============================================================================

class CallManager {
    constructor() {
        this.socket = null;
        this.audioContext = null;
        this.workletNode = null;
        this.mediaStream = null;
        this.isPlaying = false;
        this.isListening = false;

        // UI Elements
        this.startBtn = document.getElementById('startCallBtn');
        this.endBtn = document.getElementById('endCallBtn');
        this.statusText = document.getElementById('callStatusText');
        this.latencyText = document.getElementById('callLatencyText');
        this.avatar = document.querySelector('.call-avatar');
        this.languageSelect = document.getElementById('callLanguageSelect');

        // Visualizer
        this.canvas = document.getElementById('audioVisualizer');
        this.canvasCtx = this.canvas.getContext('2d');

        this.setupEventListeners();
    }

    setupEventListeners() {
        this.startBtn.addEventListener('click', () => this.startCall());
        this.endBtn.addEventListener('click', () => this.endCall());
    }

    async startCall() {
        try {
            this.updateUI('connecting');

            // 1. Initialize Audio Context
            this.audioContext = new (window.AudioContext || window.webkitAudioContext)({
                sampleRate: 24000,
                latencyHint: 'interactive'
            });

            // 2. Connect WebSocket
            const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            const wsUrl = `${protocol}//${window.location.host}/ws/call`;

            this.socket = new WebSocket(wsUrl);
            this.socket.binaryType = 'arraybuffer';

            this.socket.onopen = () => {
                console.log('WebSocket connected');
                // Send initial config
                const config = {
                    type: 'config',
                    language: this.languageSelect.value,
                    sample_rate: this.audioContext.sampleRate
                };
                this.socket.send(JSON.stringify(config));
                this.startMicrophone();
            };

            this.socket.onmessage = async (event) => {
                const data = event.data;

                if (typeof data === 'string') {
                    // JSON message
                    const msg = JSON.parse(data);
                    this.handleSignal(msg);
                } else if (data instanceof ArrayBuffer) {
                    // Audio Data (PCM Int16)
                    this.handleAudio(data);
                }
            };

            this.socket.onclose = () => {
                console.log('WebSocket disconnected');
                this.endCall();
            };

            this.socket.onerror = (error) => {
                console.error('WebSocket error:', error);
                this.updateUI('error');
            };

        } catch (e) {
            console.error('Failed to start call:', e);
            this.updateUI('error');
        }
    }

    async startMicrophone() {
        try {
            this.mediaStream = await navigator.mediaDevices.getUserMedia({
                audio: {
                    echoCancellation: true,
                    noiseSuppression: true,
                    autoGainControl: true,
                    channelCount: 1,
                    sampleRate: 16000
                }
            });

            // Use ScriptProcessor for broad compatibility (or AudioWorklet if preferred)
            // For this demo, ScriptProcessor is easier to bundle without external files
            const source = this.audioContext.createMediaStreamSource(this.mediaStream);
            const processor = this.audioContext.createScriptProcessor(4096, 1, 1);

            processor.onaudioprocess = (e) => {
                if (this.socket && this.socket.readyState === WebSocket.OPEN) {
                    const inputData = e.inputBuffer.getChannelData(0);
                    // Downsample to 16kHz if needed, but we requested 16kHz
                    // Convert float32 to int16
                    const pcmData = this.floatTo16BitPCM(inputData);
                    this.socket.send(pcmData);

                    // Visualize
                    this.visualize(inputData);
                }
            };

            source.connect(processor);
            processor.connect(this.audioContext.destination); // Needed for chrome to fire events

            this.updateUI('listening');

        } catch (e) {
            console.error('Mic error:', e);
            let errorMessage = 'Microphone access failed';

            if (e.name === 'NotAllowedError' || e.name === 'PermissionDeniedError') {
                errorMessage = 'Microphone permission denied. Please allow access.';
                // Explicitly show toast or alert for permission
                alert("Microphone permission denied. Please check your browser settings and System Privacy settings to allow microphone access.");
            } else if (e.name === 'NotFoundError') {
                errorMessage = 'No microphone found.';
            }

            this.statusText.textContent = errorMessage;
            this.statusText.style.color = "var(--error-red)";
            this.updateUI('error');

            // Close socket if open since we can't send audio
            if (this.socket) {
                this.socket.close();
            }
        }
    }

    handleSignal(msg) {
        if (msg.type === 'status') {
            if (msg.state === 'listening') {
                this.updateUI('listening');
                this.avatar.classList.remove('speaking');
            } else if (msg.state === 'speaking') {
                this.updateUI('speaking');
                this.avatar.classList.add('speaking');
            } else if (msg.state === 'thinking') {
                this.updateUI('thinking');
            }
        } else if (msg.type === 'latency') {
            this.latencyText.textContent = `Latency: ${msg.value}ms`;
        } else if (msg.type === 'error') {
            this.statusText.textContent = msg.message || 'Something went wrong';
            this.statusText.style.color = 'var(--error-red)';
        }
    }

    // Audio Queue for smooth playback
    audioQueue = [];
    nextStartTime = 0;

    async handleAudio(arrayBuffer) {
        // Play audio from server
        // Expecting 24kHz Int16 PCM (or whatever piper outputs)
        // We set AudioContext to 24000 in constructor

        const float32 = this.int16ToFloat32(arrayBuffer);
        const audioBuffer = this.audioContext.createBuffer(1, float32.length, 24000);
        audioBuffer.getChannelData(0).set(float32);

        const source = this.audioContext.createBufferSource();
        source.buffer = audioBuffer;
        source.connect(this.audioContext.destination);

        // Simple queueing logic
        const currentTime = this.audioContext.currentTime;
        if (this.nextStartTime < currentTime) {
            this.nextStartTime = currentTime + 0.05; // Small buffer
        }

        source.start(this.nextStartTime);
        this.nextStartTime += audioBuffer.duration;
    }

    endCall() {
        this.updateUI('disconnected');

        if (this.socket) {
            this.socket.close();
            this.socket = null;
        }

        if (this.mediaStream) {
            this.mediaStream.getTracks().forEach(track => track.stop());
            this.mediaStream = null;
        }

        this.nextStartTime = 0;
    }

    updateUI(state) {
        this.startBtn.disabled = state !== 'disconnected' && state !== 'error';
        this.endBtn.disabled = state === 'disconnected' || state === 'error';

        switch (state) {
            case 'connecting':
                this.statusText.textContent = "Connecting...";
                this.statusText.style.color = "var(--gray-600)";
                break;
            case 'listening':
                this.statusText.textContent = "Listening...";
                this.statusText.style.color = "var(--primary-blue)";
                break;
            case 'thinking':
                this.statusText.textContent = "Thinking...";
                this.statusText.style.color = "var(--warning-orange)";
                break;
            case 'speaking':
                this.statusText.textContent = "Apollo Speaking";
                this.statusText.style.color = "var(--success-green)";
                break;
            case 'disconnected':
                this.statusText.textContent = "Ready to Call";
                this.statusText.style.color = "var(--gray-800)";
                this.avatar.classList.remove('speaking');
                this.latencyText.textContent = "latency: --ms";
                break;
        }
    }

    floatTo16BitPCM(input) {
        const output = new Int16Array(input.length);
        for (let i = 0; i < input.length; i++) {
            const s = Math.max(-1, Math.min(1, input[i]));
            output[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
        }
        return output.buffer;
    }

    int16ToFloat32(inputArrayBuffer) {
        const int16 = new Int16Array(inputArrayBuffer);
        const float32 = new Float32Array(int16.length);
        for (let i = 0; i < int16.length; i++) {
            float32[i] = int16[i] / 32768.0;
        }
        return float32;
    }

    visualize(data) {
        const WIDTH = this.canvas.width;
        const HEIGHT = this.canvas.height;

        this.canvasCtx.fillStyle = 'rgb(249, 250, 251)'; // gray-50
        this.canvasCtx.fillRect(0, 0, WIDTH, HEIGHT);

        this.canvasCtx.lineWidth = 2;
        this.canvasCtx.strokeStyle = 'rgb(26, 86, 219)'; // primary-blue
        this.canvasCtx.beginPath();

        const sliceWidth = WIDTH * 1.0 / data.length;
        let x = 0;

        // Downsample for visualizer if data is too large
        const step = Math.ceil(data.length / WIDTH);

        for (let i = 0; i < data.length; i += step) {
            const v = data[i] * 10; // Gain for visual
            const y = (0.5 + v / 2) * HEIGHT;

            if (i === 0) {
                this.canvasCtx.moveTo(x, y);
            } else {
                this.canvasCtx.lineTo(x, y);
            }

            x += sliceWidth * step;
        }

        this.canvasCtx.lineTo(this.canvas.width, this.canvas.height / 2);
        this.canvasCtx.stroke();
    }
}

// Instantiate
const callManager = new CallManager();
