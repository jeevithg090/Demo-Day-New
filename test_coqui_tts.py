"""
Test Coqui TTS for Hindi voice quality
"""
import torch
# Workaround for PyTorch 2.6 weights_only=True default issue
import torch.serialization
_original_load = torch.load
torch.load = lambda *args, **kwargs: _original_load(*args, **{**kwargs, 'weights_only': False})

from TTS.api import TTS
import os
import time

def test_coqui_tts():
    print("Testing Coqui TTS for Hindi...")
    start = time.time()
    
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"Using device: {device}")
    
    # List available models
    print("Loading TTS model...")
    load_start = time.time()
    
    # Use XTTS v2 multilingual model (supports Hindi)
    tts = TTS("tts_models/multilingual/multi-dataset/xtts_v2").to(device)
    
    print(f"Model loaded in {time.time() - load_start:.2f}s")
    
    # Hindi text
    text = "मैं डॉ. राज से मिलना चाहता हूँ"
    
    try:
        print(f"Generating audio for: {text.encode('utf-8', errors='ignore').decode('utf-8')}")
    except:
        print("Generating audio for: [Hindi Text]")
    
    gen_start = time.time()
    
    # Generate speech
    output_file = "coi_hindi_test.wav"
    # XTTS requires a speaker reference audio file for voice cloning
    speaker_wav = "indic_parler_test.wav"
    
    if not os.path.exists(speaker_wav):
        print(f"Error: Speaker reference file '{speaker_wav}' not found.")
        return

    tts.tts_to_file(
        text=text,
        file_path=output_file,
        language="hi",
        speaker_wav=speaker_wav
    )
    
    print(f"Generation time: {time.time() - gen_start:.2f}s")
    
    abs_path = os.path.abspath(output_file)
    print(f"Audio saved to: {abs_path}")
    print(f"Total time: {time.time() - start:.2f}s")

if __name__ == "__main__":
    test_coqui_tts()
