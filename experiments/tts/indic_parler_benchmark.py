
import torch
from parler_tts import ParlerTTSForConditionalGeneration
from transformers import AutoTokenizer
import soundfile as sf
import time
import os

def test_indic_parler():
    print("Initializing Indic Parler TTS test...")
    start_time = time.time()
    
    device = "cuda:0" if torch.cuda.is_available() else "cpu"
    print(f"Using device: {device}")
    
    # Load model and tokenizer
    # Using the standard ai4bharat/indic-parler-tts model
    model_name = "ai4bharat/indic-parler-tts"
    
    print(f"Loading model: {model_name}")
    load_start = time.time()
    try:
        # Using float32 for better audio quality
        model = ParlerTTSForConditionalGeneration.from_pretrained(model_name).to(device)
        tokenizer = AutoTokenizer.from_pretrained(model_name)
    except Exception as e:
        print(f"Failed to load model: {e}")
        return
    load_end = time.time()
    print(f"Model loaded successfully in {load_end - load_start:.2f}s")

    # Warmup (important for first-run latency)
    print("Warming up model...")
    warmup_text = "Warmup"
    try:
        w_in = tokenizer("Warmup description", return_tensors="pt").input_ids.to(device)
        w_pmt = tokenizer(warmup_text, return_tensors="pt").input_ids.to(device)
        model.generate(input_ids=w_in, prompt_input_ids=w_pmt, max_new_tokens=2)
    except Exception as e:
        print(f"Warmup warning: {e}")
    
    # Test text (Hindi) - Use Devanagari script
    prompt = "नमस्ते आप कैसे हैं? यह एक परीक्षण है।"
    # Simple description - no speed/pitch modifiers as user requested
    description = "A clear female voice speaking Hindi."
    
    try:
        print(f"Generating audio for prompt: {prompt.encode('utf-8', errors='ignore').decode('utf-8')}")
    except:
        print("Generating audio for prompt: [Hindi Text]")
    
    gen_start = time.time()
    try:
        # Pass attention masks for optimization
        input_tokens = tokenizer(description, return_tensors="pt", padding=True)
        prompt_tokens = tokenizer(prompt, return_tensors="pt", padding=True)
        
        input_ids = input_tokens.input_ids.to(device)
        attention_mask = input_tokens.attention_mask.to(device)
        prompt_input_ids = prompt_tokens.input_ids.to(device)
        prompt_attention_mask = prompt_tokens.attention_mask.to(device)
        
        # Generation parameters for quality (do_sample=True, temp=0.7)
        generation = model.generate(
            input_ids=input_ids,
            attention_mask=attention_mask,
            prompt_input_ids=prompt_input_ids,
            prompt_attention_mask=prompt_attention_mask,
            do_sample=True,
            temperature=0.5,
            min_new_tokens=10
        )
        # Critical: Cast back to float32 for saving, as soundfile/numpy don't like float16 output
        audio_arr = generation.cpu().to(torch.float32).numpy().squeeze()
        
        output_file = "indic_parler_gpu_test.wav"
        sf.write(output_file, audio_arr, model.config.sampling_rate)
        
        abs_path = os.path.abspath(output_file)
        print(f"Audio generated and saved to: {abs_path}")
        
    except Exception as e:
        print(f"Generation failed: {e}")

    print(f"Total time: {time.time() - start_time:.2f}s")

if __name__ == "__main__":
    test_indic_parler()
