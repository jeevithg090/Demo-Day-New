
import time
import asyncio
import logging
import json
import base64
import numpy as np
import torch
from typing import Dict, List, Optional
from collections import deque

logger = logging.getLogger(__name__)

class AudioBuffer:
    """Thread-safe buffer for audio chunks"""
    def __init__(self):
        self.buffer = bytearray()
        self.lock = asyncio.Lock()
        
    async def append(self, data: bytes):
        async with self.lock:
            self.buffer.extend(data)
            
    async def get(self) -> bytes:
        async with self.lock:
            data = bytes(self.buffer)
            self.buffer.clear()
            return data
            
    def clear(self):
        self.buffer.clear()

class StreamingSession:
    """Manages a single AI voice call session"""
    def __init__(self, session_id: str, language: str = 'en'):
        self.session_id = session_id
        self.language = language
        self.audio_buffer = AudioBuffer()
        self.state = "listening"  # listening, thinking, speaking
        self.history = []
        self.last_speech_time = 0
        self.silence_threshold = 0.5 # seconds
        self.speech_chunks = []
        self.is_speaking = False
        
        # Latency tracking
        self.latencies = {}
        
    def reset_for_new_turn(self):
        self.speech_chunks = []
        self.audio_buffer.clear()
        
    async def process_incoming_audio(self, chunk: bytes, vad_model, vad_utils):
        """
        Process raw audio chunk:
        1. VAD check
        2. If speaking -> buffer
        3. If silence -> trigger response if buffered sufficient audio
        """
        # Convert bytes to float32 numpy array for VAD
        # Assuming 16kHz mono int16 input
        audio_int16 = np.frombuffer(chunk, dtype=np.int16)
        audio_float32 = audio_int16.astype(np.float32) / 32768.0
        
        # Run VAD (simplified for single chunk, ideally maintain state)
        # Using simplified energy/VAD check for barge-in
        energy = np.mean(np.abs(audio_float32))
        
        is_speech = energy > 0.01  # Simple threshold, should use Silero in prod
        
        if is_speech:
            self.last_speech_time = time.time()
            self.speech_chunks.append(chunk)
            
            # Barge-in: If AI is speaking, stop it
            if self.state == "speaking":
                logger.info("Barge-in detected!")
                self.state = "listening"
                return {"type": "interrupt"}
                
        else:
            # Silence detection
            if self.speech_chunks and (time.time() - self.last_speech_time > self.silence_threshold):
                # End of turn
                full_audio = b"".join(self.speech_chunks)
                self.speech_chunks = []
                self.state = "thinking"
                return {"type": "turn_complete", "audio": full_audio}
                
        return None

