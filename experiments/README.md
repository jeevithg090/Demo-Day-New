# Experiments

Research material from building the assistant. None of it is imported by the app.

- `pipeline_research.ipynb`: Colab notebook used to prototype the pipeline (Silero VAD,
  Whisper language detection, Layer-1 signals, LLaMA/Mistral/Sarvam LLM trials, Indic
  Parler-TTS and Edge TTS). Outputs are cleared. Cells that log in to Hugging Face read the
  token from the `HF_TOKEN` environment variable.
- `tts/*_benchmark.py`: standalone scripts that compared TTS engines (Coqui, Indic Parler,
  IndicBART, Piper, Silero) for Hindi/Telugu quality and latency. Each has its own extra
  dependencies, which are not in `requirements.txt`.

Run scripts from the repository root, for example `python experiments/tts/piper_tts_benchmark.py`.
