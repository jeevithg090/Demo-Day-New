"""
Test Piper TTS for Hindi - Ultra-fast local TTS (~50-100ms)
"""
import sys
import io
import os
import time
import wave
import subprocess

# Fix Windows console encoding for Hindi text
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

def download_voice_model():
    """Download Hindi voice model if not exists"""
    import urllib.request
    
    voice_dir = "piper_voices"
    os.makedirs(voice_dir, exist_ok=True)
    
    # Use rohan medium voice
    base_url = "https://huggingface.co/rhasspy/piper-voices/resolve/main/hi/hi_IN/rohan/medium"
    model_name = "hi_IN-rohan-medium"
    
    model_file = os.path.join(voice_dir, f"{model_name}.onnx")
    config_file = os.path.join(voice_dir, f"{model_name}.onnx.json")
    
    if not os.path.exists(model_file):
        print(f"Downloading {model_name}.onnx...")
        url = f"{base_url}/{model_name}.onnx"
        try:
            urllib.request.urlretrieve(url, model_file)
            print(f"Downloaded: {model_file}")
        except Exception as e:
            print(f"Error downloading model: {e}")
            return None, None
    
    if not os.path.exists(config_file):
        print(f"Downloading {model_name}.onnx.json...")
        url = f"{base_url}/{model_name}.onnx.json"
        try:
            urllib.request.urlretrieve(url, config_file)
            print(f"Downloaded: {config_file}")
        except Exception as e:
            print(f"Error downloading config: {e}")
            return None, None
    
    return model_file, config_file

def test_piper_tts():
    print("Testing Piper TTS for Hindi...")
    print("="*50)
    start = time.time()
    
    # Download voice model if needed
    model_file, config_file = download_voice_model()
    if not model_file:
        print("Failed to download voice model")
        return
    
    print(f"\nModel file: {model_file}")
    print(f"Config file: {config_file}")
    
    # Test text in Hindi
    test_texts = [
        "नमस्ते, आप कैसे हैं?",
        "मैं डॉक्टर राज से मिलना चाहता हूँ",
        "अपॉइंटमेंट के लिए कृपया रिसेप्शन पर संपर्क करें"
    ]
    
    # Import piper
    try:
        from piper import PiperVoice
        import numpy as np
        import soundfile as sf
        
        # Load the voice
        print("\nLoading Piper voice model...")
        load_start = time.time()
        voice = PiperVoice.load(model_file, config_file)
        print(f"Model loaded in {time.time() - load_start:.2f}s")
        print(f"Sample rate: {voice.config.sample_rate}")
        
        for i, text in enumerate(test_texts):
            print(f"\n--- Test {i+1} ---")
            print(f"Text: {text}")
            
            output_file = f"piper_hindi_test_{i+1}.wav"
            
            # Generate audio and measure time
            gen_start = time.time()
            
            # synthesize() returns AudioChunk objects
            audio_data = []
            for audio_chunk in voice.synthesize(text):
                # AudioChunk has audio_float_array attribute
                audio_data.append(audio_chunk.audio_float_array)
            
            gen_time = (time.time() - gen_start) * 1000
            print(f"Generation time: {gen_time:.0f}ms")
            
            # Combine all chunks, convert to int16, and save
            audio_float = np.concatenate(audio_data) if audio_data else np.array([], dtype=np.float32)
            audio_int16 = (audio_float * 32767).astype(np.int16)
            sf.write(output_file, audio_int16, voice.config.sample_rate)
            
            duration = len(audio_int16) / voice.config.sample_rate
            print(f"Audio duration: {duration:.2f}s")
            print(f"Output saved: {os.path.abspath(output_file)}")
        
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
    
    print(f"\nTotal time: {time.time() - start:.2f}s")

if __name__ == "__main__":
    test_piper_tts()
