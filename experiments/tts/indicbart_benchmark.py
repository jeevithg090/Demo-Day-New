"""
Test script to download and verify ai4bharat/IndicBART model
"""
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import torch
from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
import time

print("=" * 60)
print("IndicBART Model Download & Test")
print("=" * 60)

# Check CUDA
device = "cuda" if torch.cuda.is_available() else "cpu"
print(f"\n> Device: {device}")

# Download model
print("\n> Downloading ai4bharat/IndicBART...")
print("  (This may take a few minutes, ~1.5GB)")

start = time.time()

try:
    model_name = "ai4bharat/IndicBART"
    
    print("\n1. Loading tokenizer...")
    tokenizer = AutoTokenizer.from_pretrained(model_name, use_fast=False)
    print("   > Tokenizer loaded")
    print(f"   Tokenizer type: {type(tokenizer).__name__}")
    
    print("\n2. Loading model...")
    model = AutoModelForSeq2SeqLM.from_pretrained(
        model_name,
        torch_dtype=torch.float16 if device == "cuda" else torch.float32,
        device_map="auto" if device == "cuda" else None
    )
    
    if device == "cpu":
        model = model.to(device)
    
    print(f"   > Model loaded on {device}")
    
    load_time = time.time() - start
    print(f"\n> Total load time: {load_time:.2f}s")
    
    # Test generation
    print("\n" + "=" * 60)
    print("Testing Hindi Generation")
    print("=" * 60)
    
    test_text = "मुझे बुखार है। मुझे क्या करना चाहिए?"
    print(f"\nInput: {test_text}")
    
    # Tokenize
    tokenizer.src_lang = "hi_IN"
    inputs = tokenizer(test_text, return_tensors="pt").to(device)
    
    # Generate
    gen_start = time.time()
    
    # Set target language
    forced_bos_token_id = tokenizer.convert_tokens_to_ids("hi_IN")
    
    outputs = model.generate(
        **inputs,
        max_length=100,
        num_beams=4,
        early_stopping=True,
        forced_bos_token_id=forced_bos_token_id
    )
    gen_time = (time.time() - gen_start) * 1000
    
    response = tokenizer.decode(outputs[0], skip_special_tokens=True)
    print(f"\nOutput: {response}")
    print(f"Generation time: {gen_time:.0f}ms")
    
    print("\n" + "=" * 60)
    print("SUCCESS: Model downloaded and verified!")
    print("=" * 60)
    
    # Model info
    param_count = sum(p.numel() for p in model.parameters()) / 1e6
    print(f"\nModel parameters: {param_count:.1f}M")
    print(f"Model dtype: {model.dtype}")
    print(f"Device: {next(model.parameters()).device}")
    
except Exception as e:
    print(f"\nERROR: {e}")
    import traceback
    traceback.print_exc()
