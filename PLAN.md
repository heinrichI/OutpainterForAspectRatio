# Implementation Plan — `OutpainterForAspectRatio2`

## Goal

A standalone Python script + `.bat` launcher that batch-outpaints every image in a user-chosen folder to a target aspect ratio, using the **already-installed** krita-ai-diffusion backend (`j:\AI_Image_Enchance\ai_diffusion\server\`) with `flux-2-klein-4b-fp8` + `flux-2-klein-4B-outpaint-lora`, mirroring the plugin's working **Expand** workflow. Output saved to `output/`. Only **two** sides are expanded (auto: wider target → left/right; taller target → top/bottom). Target ratio from `.env`, default = current screen ratio.

---

## 1. Verified environment facts (all confirmed on disk)

| Item | Value |
|---|---|
| Server root | `j:\AI_Image_Enchance\ai_diffusion\server\` (bundle 1.52.0, ComfyUI 0.26.0) |
| ComfyUI dir | `server\ComfyUI\`; models mapped from `server\models\` via `extra_model_paths.yaml` (`krita-managed` → `../models`) |
| Python | `server\venv\Scripts\python.exe` |
| Start cmd | `python -su main.py` with cwd = `server\ComfyUI\`, port 8188 (`server.py:613-616`) |
| Diffusion model | `models\diffusion_models\flux-2-klein-4b-fp8.safetensors` (base, undistilled) |
| Text encoder | `models\text_encoders\Qwen3-4B-Q4_K_M.gguf` → **must use `CLIPLoaderGGUF`** (user decision) |
| LoRA | `models\loras\flux-2-klein-4B-outpaint-lora.safetensors`, strength **1.0** (plugin default via `inpaint_control`, workflow.py:1006) |
| VAE | `models\vae\flux2-vae.safetensors` |
| Custom nodes | `comfyui-tooling-nodes` (ETN_*), `comfyui-inpaint-nodes` (INPAINT_*), `ComfyUI-GGUF` — all installed under `server\ComfyUI\custom_nodes\` |
| API | `POST /prompt`, `PUT /api/etn/image/{id}`, `GET /api/etn/image/{id}`, WS `/ws?clientId=` |
| Image cache id | `crc32(PNG bytes)` formatted `%08x` (comfy_workflow.py:271-275) |
| Prompt (exact) | `Fill the green spaces according to the image.` |
| Sampler preset | `Flux 2 - Euler` = sampler `euler`, scheduler `flux2` (`Flux2Scheduler`), steps 20, cfg 5.0 (samplers.json:106-112) — use for **base** model (fp8), NOT the distilled 4-step/CFG-1 settings of `edit-flux2.json` |

---

## 2. Files to create in `f:\E\SourcePython3\OutpainterForAspectRatio2\`

```
outpainter/
  .env                  # all settings (user-editable)
  process_images.bat    # launcher: sets dir, starts server if needed, runs script
  run_outpaint.py       # single-file script (stdlib + Pillow + requests + websockets)
  README.md             # usage (Russian, matching user's language)
```

Minimal dependency set: `Pillow`, `requests`, `websockets` (all already present in `server\venv`; script runs with **that** venv's python so no pip install needed).

---

## 3. Geometry (two-side expansion, user-confirmed rule)

Input image `(iw, ih)`, target ratio `r = W_t / H_t` (e.g. 16/9, or from screen `width/height`):

- `input_ratio = iw / ih`
- If `|r - input_ratio| <= tolerance` (e.g. 0.01) → skip (or still save a copy, configurable `SKIP_IF_MATCH=true`).
- If `r > input_ratio` (**wider**): keep `H = ih`; `W = round(ih * r)`; round both up to multiple of 16. Expand **left/right**: `dx_left = (W - iw)//2`, `dx_right = W - iw - dx_left`.
- If `r < input_ratio` (**taller**): keep `W = iw`; `H = round(iw / r)`; round to multiple of 16. Expand **top/bottom** similarly.
- Border ratio guard: if `max(dx_left, dx_right) > BORDER_MAX_RATIO * iw` (or vertical analog, default 0.25 per LoRA model card) → warn and either skip (`SKIP_LARGE_BORDER=true`, default) or proceed.
- Output resolution cap: `MAX_PIXELS` (default 6 MP, matches plugin `max_pixel_count=6`) and `MAX_LONG_SIDE` (default 2048) — shrink the *added* canvas by scaling down the whole composite input before diffusion if exceeded (plugin caps inpaint context ~512²–2048² px; 4090/24 GB handles this).

Build (all in-memory with Pillow):
- **Canvas**: original pasted at offset `(dx_left, dy_top)` on a solid **pure green `#00FF00`** background of size `(W, H)` — matches `FillMode.green` = `EmptyImage(color=65280)` (workflow.py:921-924).
- **Mask**: white `(255)` in the added border region, black `(0)` over the original (grayscale PNG). This is the `in_mask`; the green fill mask is derived in-graph by `ThresholdMask(0.99)` like the plugin.

---

## 4. ComfyUI node graph (mirrors `workflow.inpaint()` flux2_4b + LoRA + green fill + ReferenceLatent path)

Exact node sequence to build per image (ids auto-numbered by a small `ComfyWorkflow`-like builder):

1. `UNETLoader(unet_name="flux-2-klein-4b-fp8.safetensors", weight_dtype="default")`
2. `LoraLoaderModelOnly(model=1, lora_name="flux-2-klein-4B-outpaint-lora.safetensors", strength_model=1.0)` — LoRA right after model load (plugin applies it via `apply_control` strength 1.0)
3. `CLIPLoaderGGUF(clip_name="Qwen3-4B-Q4_K_M.gguf", type="flux2")`
4. `VAELoader(vae_name="flux2-vae.safetensors")`
5. `DifferentialDiffusion(model=2)`
6. `ETN_LoadImageCache(id=<crc32 canvas>)` → `img` (RGB canvas w/ green border)
7. `ETN_LoadImageCache(id=<crc32 mask>)` → `mask` (second output — grayscale border mask)
8. `INPAINT_ExpandMask(mask=7, grow=GROW, blur=FEATHER, blur_type="linear")` — defaults grow=16, feather=8 (configurable; plugin derives from selection feather)
9. `CropMask(mask=8, x=0, y=0, width=W, height=H)` — target_bounds = full canvas
10. `INPAINT_StabilizeMask(mask=9, epsilon=0.01)` → `inpaint_mask` (scaled-to-initial; initial==target in our single-pass mode)
11. `ThresholdMask(mask=8, value=0.99)` → fill mask
12. `EmptyImage(width=W, height=H, color=65280)` → green
13. `ImageCompositeMasked(source=12, destination=6, mask=11, x=0, y=0, resize_source=false)` → green-filled canvas (FillMode.green)
14. `VAEEncode(vae=4, pixels=13)` → latent
15. `SetLatentNoiseMask(samples=14, mask=10)` → latent
16. `CLIPTextEncode(clip=3, text="Fill the green spaces according to the image.")` → positive
17. `CLIPTextEncode(clip=3, text="")` → negative
18. `ReferenceLatent(conditioning=16, latent=15)` → positive
19. `ReferenceLatent(conditioning=17, latent=15)` → negative
20. `FluxGuidance(conditioning=18, guidance=CFG)` (guidance = CFG if >1 else 3.5, plugin `sampler_custom_advanced`)
21. `BasicGuider(model=5, conditioning=20)`
22. `Flux2Scheduler(steps=STEPS, width=W, height=H)`
23. `KSamplerSelect(sampler_name="euler")`
24. `RandomNoise(noise_seed=<seed>)`
25. `SamplerCustomAdvanced(noise=24, guider=21, sampler=23, sigmas=22, latent_image=15)` → output `[1]` (samples)
26. `VAEDecode(vae=4, samples=25)`
27. `ETN_SaveImageCache(images=26, format="PNG")` → output node

Notes:
- **Skip** `get_inpaint_reference` cropping and IPAdapter — for `flux2_4b` the reference control is a no-op in `apply_style_models` (only Arch.flux handled); the real reference mechanism is `ReferenceLatent` (nodes 18-19), which we include.
- **Skip** ColorMatch by default (`misc.color_match` default = 0.0 in plugin); make `COLOR_MATCH=true/false` in `.env` → insert `INPAINT_ColorMatch(target=26, reference=13, exclude_mask=10, strength=1.0)` before node 27.
- **Skip** `ETN_ApplyMaskToImage` compositing — it only alpha-masks the result for Krita; with `SetLatentNoiseMask` the unmasked original pixels are already preserved in the decode. Save the full decoded image directly.
- Optional `SaveImage`-style fallback: if `ETN_` cache retrieval fails, one can add a plain `SaveImage` node and fetch via ComfyUI's `/view?filename=...`; keep as fallback only.

---

## 5. Runner flow (`run_outpaint.py`)

1. Load `.env` (no python-dotenv dependency — tiny parser, or use `dotenv` since it may exist; keep stdlib-only parser to be safe).
2. Compute target ratio: `ASPECT_RATIO` value — `screen` (default, via `ctypes.windll.user32.GetSystemMetrics(0/1)` with `[1920,1080]` fallback), `WxH` (e.g. `16x9`), or float.
3. **Ensure server running** (user answer: auto-start):
   - `GET http://127.0.0.1:8188/system_stats` (timeout ~3 s) → if OK, reuse.
   - Else spawn `server\venv\Scripts\python.exe -su main.py` with cwd=`server\ComfyUI\`, env `ONEDNN_MAX_CPU_ISA=AVX2`, no `--lowvram` (4090/24 GB), redirect stdout to `logs\comfyui.log`; poll `/system_stats` until ready (up to 180 s).
4. Verify nodes/models once: `GET /object_info`; assert `UNETLoader`, `CLIPLoaderGGUF`, `LoraLoaderModelOnly`, `DifferentialDiffusion`, `ReferenceLatent`, `FluxGuidance`, `Flux2Scheduler`, `ETN_LoadImageCache`, `ETN_SaveImageCache`, `INPAINT_ExpandMask`, `INPAINT_StabilizeMask`, `ThresholdMask`, `EmptyImage`, `ImageCompositeMasked` exist; assert model filenames exist in `diffusion_models`/`text_encoders`/`loras`/`vae` options. Fail fast with a clear Russian error listing what's missing (avoids the old silent 400s).
5. For each image in `SRC_DIR` (png/jpg/jpeg/webp/bmp):
   - Load, compute geometry (section 3), build canvas+mask (section 3).
   - `PUT /api/etn/image/<crc32>` for canvas and mask PNGs.
   - `POST /prompt` `{prompt, client_id=<uuid>, prompt_id=<uuid>}`.
   - Wait on WS for `executed` (grab `output.images[].id`) or `execution_error`/`execution_interrupted`; log progress via `progress` messages. Timeout per image (default 900 s).
   - `GET /api/etn/image/<id>` → save `output/<name>__<W>x<H>_<ratio>.png`.
   - Fixed seed per image (`SEED` in .env, default random); `--limit`, `--only` optional CLI overrides.
6. Write `output/results.jsonl` per image: status, paths, geometry, seed, elapsed. `RESUME=true` skips entries already marked `ok`.
7. Per-image error handling: catch OOM (parse `execution_error` for `OutOfMemoryError`), log, continue; optionally `POST /interrupt` on timeout.

---

## 6. `.env` (defaults shown)

```ini
SRC_DIR=testImages            ; directory with source images
OUT_DIR=output
ASPECT_RATIO=screen           ; screen | 16x9 | 4x3 | 1.3333 ...
SCREEN_FALLBACK=1920,1080
MAX_LONG_SIDE=2048
MAX_PIXELS=6000000
STEPS=30                     ; base-model range 20-50 (50 per model card / jeat-labs)
CFG=4.0                      ; guidance 4.0 per model card; 3.5-5.0 ok
SEED=-1                      ; -1 = random per image
GROW=16
FEATHER=8
BORDER_MAX_RATIO=0.25
SKIP_LARGE_BORDER=true
SKIP_IF_MATCH=true
COLOR_MATCH=false
RESUME=true
SERVER_URL=127.0.0.1:8188
SERVER_DIR=j:\AI_Image_Enchance\ai_diffusion\server
```

`process_images.bat`:

```bat
@echo off
setlocal
set ROOT=%~dp0
cd /d "%ROOT%"
set "PY=%SERVER_DIR%\venv\Scripts\python.exe"
if not exist "%PY%" (echo venv python not found & exit /b 1)
"%PY%" run_outpaint.py %*
pause
```

---

## 7. What the new implementation must NOT repeat (lessons from `OutpainterForAspectRatio`)

1. **Wrong models** — hardcode/verify against the installed set: `flux-2-klein-4b-fp8`, `Qwen3-4B-Q4_K_M.gguf`, `flux-2-klein-4B-outpaint-lora`, `flux2-vae`. No 9B, no 8B encoder.
2. **No LoRA / wrong workflow** — this plan's graph includes `LoraLoaderModelOnly` + green fill + `ReferenceLatent` + `DifferentialDiffusion` + `Flux2Scheduler`; it does NOT use the broken `ImagePadForOutpaint`/`InpaintModelConditioning` stub.
3. **Never-started server** — auto-start from `SERVER_DIR` with the venv python, wait for `/system_stats`, then reuse.
4. **Untestable config paths** — `.env` only, single source of truth; config errors fail loudly before any submit.
5. No reliance on `pytest`/repo tests for the E2E path — add a small `--dry-run` mode that dumps one graph JSON to `output/debug/` for inspection without submitting.

---

## 8. Validation steps (during implementation)

1. Start server, run `--dry-run` on one test image → eyeball graph JSON matches section 4 node names/inputs.
2. Process one small image (e.g. 512×512 → 16:9) with 20 steps; verify output saved, original region pixel-identical where mask=0, green gone, no seams.
3. Batch run `testImages`; confirm results `ok` entries + PNGs exist on disk (old bug: results said ok with no file).
4. Check VRAM headroom on 4090 during a 2048-long-side job (should fit in 24 GB fp8; no `--lowvram`).
5. Test taller-ratio case (9:16) to confirm top/bottom expansion.

---

## 9. Deliverables summary

- `run_outpaint.py` — single-file batch runner (geometry, server auto-start, graph builder, WS job execution, PNG retrieval, results log, resume, dry-run).
- `process_images.bat` — double-click launcher using the plugin's venv python.
- `.env` — documented defaults (ratio from screen by default).
- `README.md` — setup + usage in Russian, notes on quality knobs (steps/CFG/border limits).

Estimated effort: ~600-800 lines total in `run_outpaint.py`; all reference logic verified against `workflow.py`/`comfy_workflow.py`/`comfy_client.py`/`server.py`/`nodes.py` as cited above.
