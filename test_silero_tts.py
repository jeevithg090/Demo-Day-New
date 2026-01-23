"""
Test Silero TTS for Hindi/Indic languages
Fast TTS with ~100-200ms latency
"""
import sys
import io

# Fix Windows console encoding for Hindi text
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

import torch
import time
import os
import soundfile as sf

def test_silero_tts():
    print("Testing Silero TTS for Hindi...")
    start = time.time()
    
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    print(f"Using device: {device}")
    
    # Load Silero Indic TTS model
    print("Loading Silero TTS model...")
    load_start = time.time()
    
    model, example_text = torch.hub.load(
        repo_or_dir='snakers4/silero-models',
        model='silero_tts',
        language='indic',
        speaker='v4_indic'
    )
    model.to(device)
    
    print(f"Model loaded in {time.time() - load_start:.2f}s")
    
    # Hindi text - needs to be romanized for Silero
    # Using aksharamukha for transliteration
    original_text = "मैं डॉक्टर राज से मिलना चाहता हूँ"
    
    try:
        from aksharamukha import transliterate
        roman_text = transliterate.process('Devanagari', 'ISO', original_text)
        print(f"Original: {original_text}")
        print(f"Romanized: {roman_text}")
    except ImportError:
        print("aksharamukha not installed. Using pre-romanized text.")
        # Pre-romanized version (ISO format)
        roman_text = "maiṁ ḍŏkṭar rāj se milnā cāhtā hūm̐"
        print(f"Using pre-romanized: {roman_text}")
    
    # Available speakers for Indic: hindi_male, hindi_female, 
    # tamil_male, tamil_female, telugu_male, telugu_female,
    # kannada_male, kannada_female, etc.
    speakers = ['hindi_male', 'hindi_female']
    
    for speaker in speakers:
        print(f"\n--- Testing speaker: {speaker} ---")
        gen_start = time.time()
        
        try:
            # Generate audio
            audio = model.apply_tts(
                text=roman_text,
                speaker=speaker,
                sample_rate=24000
            )
            
            gen_time = time.time() - gen_start
            print(f"Generation time: {gen_time*1000:.0f}ms")
            
            # Save audio
            output_file = f"silero_{speaker}_test.wav"
            
            # Convert tensor to numpy and save
            audio_np = audio.cpu().numpy()
            sf.write(output_file, audio_np, 24000)
            
            abs_path = os.path.abspath(output_file)
            print(f"Audio saved to: {abs_path}")
            print(f"Audio duration: {len(audio_np)/24000:.2f}s")
            
        except Exception as e:
            print(f"Error with {speaker}: {e}")
    
    print(f"\nTotal time: {time.time() - start:.2f}s")

def test_all_indic_languages():
    """Test all supported Indic languages"""
    print("\n" + "="*50)
    print("Testing all Indic languages supported by Silero")
    print("="*50)
    
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    
    model, _ = torch.hub.load(
        repo_or_dir='snakers4/silero-models',
        model='silero_tts',
        language='indic',
        speaker='v4_indic'
    )
    model.to(device)
    
    # Test texts for each language (romanized)
    test_cases = {
        'hindi_female': ('Devanagari', 'नमस्ते, आप कैसे हैं?'),
        'tamil_female': ('Tamil', 'வணக்கம், நீங்கள் எப்படி இருக்கிறீர்கள்?'),
        'telugu_female': ('Telugu', 'నమస్కారం, మీరు ఎలా ఉన్నారు?'),
        'kannada_female': ('Kannada', 'ನಮಸ್ಕಾರ, ನೀವು ಹೇಗಿದ್ದೀರಿ?'),
    }
    
    try:
        from aksharamukha import transliterate
        has_transliterator = True
    except ImportError:
        has_transliterator = False
        print("WARNING: aksharamukha not installed. Install with: pip install aksharamukha")
    
    for speaker, (script, text) in test_cases.items():
        print(f"\n--- {speaker} ({script}) ---")
        print(f"Original: {text}")
        
        if has_transliterator:
            if script == 'Tamil':
                roman_text = transliterate.process(script, 'ISO', text, pre_options=['TamilTranscribe'])
            else:
                roman_text = transliterate.process(script, 'ISO', text)
            print(f"Romanized: {roman_text}")
        else:
            print("Skipping - aksharamukha not installed")
            continue
        
        gen_start = time.time()
        try:
            audio = model.apply_tts(text=roman_text, speaker=speaker, sample_rate=24000)
            print(f"Generation time: {(time.time() - gen_start)*1000:.0f}ms")
            
            output_file = f"silero_{speaker.split('_')[0]}_test.wav"
            sf.write(output_file, audio.cpu().numpy(), 24000)
            print(f"Saved: {output_file}")
        except Exception as e:
            print(f"Error: {e}")

if __name__ == "__main__":
    test_silero_tts()
    # Uncomment to test all languages:
    # test_all_indic_languages()
