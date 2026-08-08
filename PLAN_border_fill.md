# План (факт): полное повторение пайплайна плагина Krita AI Diffusion (Expand) для FLUX.2 Klein 4B

## ВАЖНОЕ УТОЧНЕНИЕ (проверено по исходникам 2025-08)

Для нашей модели **FLUX.2 Klein 4B (flux2_4b)** плагин при Expand использует НЕ navier-stokes,
а **FillMode.green + use_inpaint_model=True**:

| Факт | Где подтверждено |
|---|---|
| `detect_inpaint()`: `case InpaintMode.fill | InpaintMode.expand, Arch.flux2_4b, True: result.fill = FillMode.green; result.use_inpaint_model = True` | `backend/workflow.py:967-968` |
| `edit_reference=True` для expand+flux2_4b ставится в `build_instructions` (промпт "Fill the green spaces...") | `backend/workflow.py:1545-1558` |
| `use_inpaint_model=True` → в кодировании используется `INPAINT_VAEEncodeInpaintConditioning` (concat-latent), а НЕ `SetLatentNoiseMask` | `backend/workflow.py:1074-1084`, `comfy_workflow.py:950-958` |
| LoRA outpaint для flux2_4b зарегистрирована как `ControlMode.inpaint` — она обучена на concat-inpaint | `backend/resources.py:809` |
| flux2_4b — это `is_flux2`, НЕ `is_flux_like` → guider = **CFGGuider**, не FluxGuidance | `backend/resources.py:219-225`, `comfy_workflow.py:340-362` |
| Зелёная заливка = канва #00FF00 (уже делается в `prepare_inputs`) | `workflow.py:921-924` |
| Многопроходности НЕТ. 2-pass hi-res refine (upscale + strength 0.4) только если контекст больше 2048/4MP | `backend/workflow.py:1103-1138`, `resolution.py` |
| Refine pass 2 использует обычный `VAEEncode + SetLatentNoiseMask` и **без ReferenceLatent** | `workflow.py:1121-1137` |
| Параметры маски: feather=10% диагонали канвы (мин 32), grow=4+feather//2, blend=min(25,grow+feather//2) | `model/model.py:1585-1606`, `settings.py:218-246` |
| Композит: `ThresholdMask(0.0) -> ShrinkMask(blend//2, blend)` из выросшей+растушёванной denoise-маски → переход ВНУТРИ фото | `workflow.py:941-945` |
| `color_match=True` по умолчанию: `INPAINT_ColorMatch(out, in_image=зелёная_канва, exclude_mask=inpaint_mask, 1.0)` | `workflow.py:1153-1156`, `settings.py` |

**Почему раньше было размыто:**
1. Navier-stokes заливка: LoRA outpaint обучена на зелёном маркере, а не на размазанных цветах.
2. `FluxGuidance` вместо `CFGGuider` — неверный guider для flux2_4b.
3. `SetLatentNoiseMask` вместо `INPAINT_VAEEncodeInpaintConditioning` — LoRA ожидает concat-latent.
4. Огромный feather (277px при канве 2416x1360) накрывал почти весь оригинал (660+142+277=1079 из 1080) → диффузия перерисовывала фото.

## Что сделано в `run_outpaint.py` (v4)

- `prepare_inputs`: канва #00FF00 (зелёная рамка) — как FillMode.green.
- `build_graph`: `INPAINT_VAEEncodeInpaintConditioning(positive, negative, vae, pixels=канва, mask=inpaint_mask)` → ReferenceLatent(pos/neg, latent=enc[3]) → CFGGuider → Flux2Scheduler/euler → SamplerCustomAdvanced(latent=enc[3]) → VAEDecode.
- Refine: pass1 concat-latent на уменьшенном разрешении; upscale (NMKD 4x) + `VAEEncode`+`SetLatentNoiseMask` + SplitSigmas(strength 0.4) — без ReferenceLatent.
- `compute_mask_params`: feather = max(round(0.10*diag), 32), но НЕ более ~6% меньшей стороны ОРИГИНАЛА (защита от перерисовки всего фото); grow=4+feather//2; blend=min(25,grow+feather//2).
- `build_compositing_mask`: grow+feather → threshold(0) → shrink(blend//2)+blur — переход внутри фото, чёрного нет.
- Убран multi-pass и median-фильтр (`denoise_border`).
- `verify_server`: проверка `CFGGuider`, `INPAINT_VAEEncodeInpaintConditioning`, upscaler (COMBO-структура `model_name`).

## Проверка

- 3 тестовых изображения (фото 1080x1350, portrait 768, square 1024) → 16:9, 20 шагов, seed 42.
- Центр оригинала структурно сохранён (резкость совпадает с исходником); рамка резче (фото: 399→814).
- Полосы (ring artifacts) и чёрные тени отсутствуют.
