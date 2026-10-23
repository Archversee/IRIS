# Voice latency: speech-to-text, the LLM, text-to-speech

A spoken question goes: tablet mic → speaker check → Whisper (`voice/stt.py`) → chat router →
Ollama (`coach/llm_coach.py`) → Kokoro (`voice/tts.py`, `runtime/speech.py`) → tablet plays the WAV.

## Where each one runs

MSFS already uses most of the GPU, so the three models are split by what each needs:

| Model | Default | Why |
|---|---|---|
| LLM (Ollama, `coach:`) | GPU | On the CPU a 7B model takes seconds just to read the prompt. |
| Whisper (`voice.stt_*`) | GPU if there is one, else CPU | `small.en` takes ~0.2 s on the GPU and ~1-2 s on the CPU. `medium` takes ~10 s on the CPU. If the GPU fails, Whisper falls back to the CPU. |
| Kokoro (`voice.tts_device`) | CPU | It's small (82M) and still several times faster than real time on the CPU. |

To move one: `voice.tts_device: cuda|cpu`, `voice.stt_device: cuda|cpu`, and for Ollama
`coach.num_gpu` (layers on the GPU: -1 = all, 0 = CPU only) and `coach.num_thread`.

## What makes it feel faster

- **Sentence by sentence.** A line of several sentences is made and played one sentence at a
  time: the tablet starts on the first while the next one is being made.
- **Said before = no synthesis.** Lines already spoken (the scripted callouts repeat every
  circuit) are played from a cache of the last 300.
- **Warm-up.** Kokoro speaks one line at start-up, so the first real line doesn't pay for loading.
  Ollama loads the coach model at start-up and keeps it loaded (`keep_alive: -1`), with the same
  options it uses later (another `num_gpu` / `num_ctx` would make it reload).
- **Fewer tokens.** The telemetry the LLM gets is rounded and named with its units.

## Finding the slow part (MAVIS log)

```
whisper: 2.9s audio -> 0.31s compute (cuda/float16)
LLM qwen2.5:7b-instruct: total 1.2s (load 0.0s, prompt 412 tok 0.2s, generate 38 tok 0.9s = 42 tok/s ...)
TTS -> tablet say_12.wav (3.4s audio, ready after 0.45s, 2 parts)
Kokoro on cpu, warm-up 1.10s
```

- `load` above 0 on every answer: the model is being reloaded. Check nothing else (the SOP
  parser, IRIS's debrief) uses a different model or `num_ctx` on the same Ollama during a flight.
- Slow `prompt` / `generate`: run `ollama ps`. Anything other than `100% GPU` means MSFS left
  too little VRAM. Use a smaller model (`qwen2.5:3b-instruct`), or point `coach.ollama_url`
  at the laptop.
- `ready after` above ~1 s on the CPU: set `voice.tts_device: cuda`, or give Kokoro more
  `tts_cpu_threads`.

## Units

All instructor lines go through `voice/units.py`: "knots" (not kt), "degrees" (not deg or °),
"feet", "feet per minute", "nautical miles", "seconds", "minutes". The spoken version also says
"R P M", "percent", "above ground level" and reads "70-80" as "70 to 80". The personas tell the
LLM to write units in full, and whatever it writes is normalised anyway. Test it with
`python -m voice.units`.
