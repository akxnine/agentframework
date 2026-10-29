# Foundry Local: tunable settings and optimizations

Only what you can actually change. Foundry Local runs ONNX Runtime GenAI models, not GGUF, and exposes no KV-cache-quantization or Flash Attention switch.

Run `python system_check.py` (or `--watch`) to see your resources and model config.

## 1. Model selection (biggest lever)
- Pick the smallest model that extracts accurately: 1.5B–8B. Compare with `foundry model ls`.
- Choose int4 variants where offered. Weights dominate memory.
- Use the GPU variant (`-generic-gpu`) when VRAM or unified memory allows; the CPU variant is slower and uses system RAM.
- Inspect options with `foundry model info <alias>` (variants, device, execution provider, size).
- Rule of thumb: model size + ~1–2 GB for KV cache and runtime must fit in free memory. On Apple Silicon, GPU memory is shared with RAM.

## 2. Context length (per request)
- Memory for the KV cache grows linearly with prompt + output tokens.
- Keep requests at about 2–8K tokens. Send one statement or page range per request, then merge the results.
- Strip headers, footers and disclaimers from the PDF text before sending it.
- Avoid the `-128k` variants (e.g. `Phi-3-mini-128k`) unless needed; prefer the `-4k` variant.

## 3. Request parameters (OpenAI-compatible API)
- `max_tokens`: cap the output so it can't grow without bound.
- `temperature=0`: deterministic output, fewer retries.
- JSON-only prompts and structured output: shorter completions and fewer parse failures.
- Concurrency: run requests one at a time (or 2) on a small GPU. Parallel requests multiply KV-cache memory.

## 4. Model lifecycle
- `foundry model load <alias>` before a run avoids first-request latency.
- `foundry model unload <alias>` frees memory when you're done or switching models.
- `foundry server stop` / `restart` clears leaked memory.
- Keep only one model loaded at a time.
- `foundry server logs` shows execution-provider and load errors.

## 5. System
- Close GPU/memory-heavy apps (browsers, Electron apps, video).
- Keep swap use near zero. If swap is in use, inference slows sharply.
- Leave 10–20% of RAM and VRAM free for the OS.
- Keep several GB of free disk for the model cache.
- Plug in laptops and disable low-power mode.
- On NVIDIA, watch `nvidia-smi`; on Mac, Activity Monitor → Memory/GPU History.

## 6. Pipeline-level (this repo)
- Add `max_tokens` and chunking in `recon/extraction.py`.
- Cache extracted JSON (`output/json/`) and skip re-extraction for unchanged PDFs.
- Run `foundry_preflight.py` once, then reuse the exported `FOUNDRY_HOST` / `FOUNDRY_MODEL`.

## Checklist
| Item | Target |
|---|---|
| Model | smallest accurate int4, GPU variant |
| Request size | <= 8K tokens |
| `max_tokens` | set |
| `temperature` | 0 |
| Loaded models | 1 |
| Swap in use | ~0 |
| Free RAM / VRAM | >= 10–20% |
