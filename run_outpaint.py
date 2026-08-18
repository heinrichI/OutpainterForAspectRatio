#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Batch outpainting to a target aspect ratio using the krita-ai-diffusion
backend (ComfyUI + FLUX.2 Klein 4B fp8 + fal outpaint LoRA).

Mirrors the plugin's "Expand" workflow exactly for FLUX.2 Klein
(FillMode.green, workflow.py:967-968):
    green canvas -> VAEEncode -> SetLatentNoiseMask -> ReferenceLatent
    -> CFGGuider -> Flux2Scheduler/euler -> SamplerCustomAdvanced
    -> VAEDecode -> (optional 2-pass hi-res refine: upscale +
       SetLatentNoiseMask, strength 0.4, no ReferenceLatent)
    -> INPAINT_ColorMatch -> ETN_SaveImageCache

The outpaint LoRA is registered as ControlMode.inpaint for flux2_4b
(resources.py:809), so find_control(inpaint) finds it and is_inpaint_model
is False: the plugin feeds the green canvas through plain VAEEncode +
SetLatentNoiseMask (workflow.py:1080-1082), NOT INPAINT_VAEEncodeInpaint-
Conditioning; and the LoRA is applied in apply_control with strength 1.0
(workflow.py:592-600). flux2 is not is_flux_like, so the guider is
CFGGuider (not FluxGuidance, comfy_workflow.py:340-362).

Single pass only (plugin scheme); no ring-based multi-pass.

HTTP-only client (no websockets dependency): jobs are submitted via
POST /prompt and results are polled from GET /history/{prompt_id}.
"""
import argparse
import ctypes
import io
import json
import math
import os
import random
import sys
import time
import uuid
import zlib
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFilter, ImageOps
    import requests
except ImportError as e:
    sys.stderr.write(f"Missing dependency: {e}\nInstall Pillow and requests.\n")
    sys.exit(2)

# ---------------------------------------------------------------------------
# Config (.env)
# ---------------------------------------------------------------------------

DEFAULTS = {
    "SRC_DIR": "testImages",
    "OUT_DIR": "output",
    "ASPECT_RATIO": "screen",          # screen | 16x9 | 4x3 | 1.3333
    "SCREEN_FALLBACK": "1920,1080",
    "MAX_LONG_SIDE": "2560",
    "MAX_PIXELS": "6000000",
    "STEPS": "50",
    "CFG": "4.0",
    "SEED": "-1",                      # -1 = random per image
    "GROW": "0",
    "FEATHER": "0",
    "BLEND": "0",
    "LORA_STRENGTH": "1.0",
    "BORDER_MAX_RATIO": "0.25",
    "SKIP_LARGE_BORDER": "false",
    "SKIP_IF_MATCH": "true",
    "COLOR_MATCH": "true",
    "REFINE_MAX_LONG": "2048",
    "REFINE_MAX_PIXELS": "4000000",
    "REFINE_UPSCALER": "4x_NMKD-Superscale-SP_178000_G.pth",
    "RESUME": "true",
    "SERVER_URL": "127.0.0.1:8188",
    "SERVER_DIR": r"j:\AI_Image_Enchance\ai_diffusion\server",
    "PROMPT": "Fill the green spaces according to the image.",
    "TIMEOUT_PER_IMAGE": "900",
}

PROMPT_FALLBACK = "Fill the green spaces according to the image."

UNET_MODEL = "flux-2-klein-4b-fp8.safetensors"
CLIP_MODEL = "Qwen3-4B-Q4_K_M.gguf"
LORA_MODEL = "flux-2-klein-4B-outpaint-lora.safetensors"
VAE_MODEL = "flux2-vae.safetensors"
UPSCALE_MODEL = "4x_NMKD-Superscale-SP_178000_G.pth"  # plugin UpscalerName.default
REFINE_STRENGTH = 0.4  # plugin hi-res refine pass strength (workflow.py:1109)

REQUIRED_NODES = [
    "UNETLoader", "LoraLoaderModelOnly", "CLIPLoaderGGUF", "VAELoader",
    "DifferentialDiffusion", "ETN_LoadImageCache", "INPAINT_ExpandMask",
    "CropMask", "INPAINT_StabilizeMask", "VAEEncode", "SetLatentNoiseMask",
    "CLIPTextEncode", "ReferenceLatent", "CFGGuider", "BasicGuider",
    "Flux2Scheduler", "KSamplerSelect", "RandomNoise", "SamplerCustomAdvanced",
    "VAEDecode", "ETN_SaveImageCache", "INPAINT_ColorMatch",
    "ImageScale", "MaskToImage", "ImageToMask", "SplitSigmas",
    "UpscaleModelLoader", "ImageUpscaleWithModel",
]

DIF_MULT = 16  # diffusion multiple
PIPE_VERSION = "v4"  # bump to force reprocessing after pipeline changes


def parse_env(path: Path) -> dict:
    cfg = dict(DEFAULTS)
    if not path.exists():
        return cfg
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ";")) or "=" not in line:
            continue
        key, _, value = line.partition("=")
        cfg[key.strip()] = value.strip()
    return cfg


def env_bool(v: str) -> bool:
    return v.strip().lower() in ("1", "true", "yes", "on")


def env_int(v: str, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def env_float(v: str, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Aspect ratio
# ---------------------------------------------------------------------------

def screen_resolution() -> tuple[int, int]:
    try:
        user32 = ctypes.windll.user32
        w, h = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
        if w > 0 and h > 0:
            return w, h
    except Exception:
        pass
    return 0, 0


def parse_aspect_ratio(cfg: dict) -> tuple[int, int]:
    """Return (width, height) of the target aspect ratio."""
    raw = str(cfg.get("ASPECT_RATIO", "screen")).strip().lower()
    if raw in ("screen", "monitor", "display", ""):
        w, h = screen_resolution()
        if w <= 0 or h <= 0:
            try:
                parts = str(cfg.get("SCREEN_FALLBACK", "1920,1080")).split(",")
                w, h = int(parts[0]), int(parts[1])
            except (ValueError, IndexError):
                w, h = 1920, 1080
        return w, h
    raw = raw.replace(":", "x").replace("/", "x")
    if "x" in raw:
        a, _, b = raw.partition("x")
        return int(float(a)), int(float(b))
    # plain float ratio
    r = float(raw)
    return round(r * 1000), 1000


def compute_geometry(iw: int, ih: int, target_w: int, target_h: int,
                     max_long: int, max_pixels: int, tol: float = 0.01):
    """Compute final canvas and placement of the original image.

    Expands only two sides: wider target -> left/right, taller -> top/bottom.
    Returns dict with W,H, offsets and original region size.
    """
    in_ratio = iw / ih
    out_ratio = target_w / target_h

    if abs(out_ratio - in_ratio) <= tol:
        return None  # ratio already matches

    if out_ratio > in_ratio:
        # wider: keep height, extend left/right
        ideal_w = ih * out_ratio
        ideal_h = ih
    else:
        # taller: keep width, extend top/bottom
        ideal_w = iw
        ideal_h = iw / out_ratio

    # resolution caps (mirrors plugin max_pixel_count / checkpoint limits)
    scale = 1.0
    if ideal_w > max_long or ideal_h > max_long:
        scale = min(scale, max_long / ideal_w, max_long / ideal_h)
    if ideal_w * ideal_h > max_pixels:
        scale = min(scale, math.sqrt(max_pixels / (ideal_w * ideal_h)))

    def mult16(v: float) -> int:
        # round UP so the canvas never shrinks below the original content
        return max(DIF_MULT, int(math.ceil(v / DIF_MULT)) * DIF_MULT)

    W = mult16(ideal_w * scale)
    H = mult16(ideal_h * scale)

    # exact target output size (diffusion canvas is cropped back to this,
    # like the plugin's crop_image(out_image, target_bounds))
    TW = max(DIF_MULT, round(ideal_w * scale))
    TH = max(DIF_MULT, round(ideal_h * scale))

    # original image scaled by the cap factor, then centered in the canvas
    s = scale
    new_iw = max(1, round(iw * s))
    new_ih = max(1, round(ih * s))
    dx_left = (W - new_iw) // 2
    dy_top = (H - new_ih) // 2
    dx_right = W - new_iw - dx_left
    dy_bottom = H - new_ih - dy_top
    if dx_right < 0:
        dx_left += dx_right
        dx_right = 0
    if dy_bottom < 0:
        dy_top += dy_bottom
        dy_bottom = 0

    crop_x = max(0, (W - TW) // 2)
    crop_y = max(0, (H - TH) // 2)

    border = max(dx_left, dx_right, dy_top, dy_bottom)
    border_ratio = border / min(iw, ih) if min(iw, ih) > 0 else 1.0

    return {
        "W": W, "H": H,
        "TW": TW, "TH": TH,
        "crop_x": crop_x, "crop_y": crop_y,
        "iw": new_iw, "ih": new_ih,
        "dx_left": dx_left, "dx_right": dx_right,
        "dy_top": dy_top, "dy_bottom": dy_bottom,
        "border": border, "border_ratio": border_ratio,
        "scaled": scale < 0.999,
    }


def prepare_inputs(image: Image.Image, geo: dict):
    """Build the green canvas and border mask as PIL images, plus the
    pristine original at output resolution (needed for compositing).

    Canvas: original pasted on a pure green (#00FF00) border.
    Mask:   white in the border region, black over the original.
    """
    W, H = geo["W"], geo["H"]
    orig = ImageOps.exif_transpose(image)
    if orig.mode != "RGB":
        orig = orig.convert("RGB")
    if orig.size != (geo["iw"], geo["ih"]):
        orig = orig.resize((geo["iw"], geo["ih"]), Image.LANCZOS)

    canvas = Image.new("RGB", (W, H), (0, 255, 0))
    canvas.paste(orig, (geo["dx_left"], geo["dy_top"]))

    mask = Image.new("L", (W, H), 255)
    draw = ImageDraw.Draw(mask)
    draw.rectangle(
        [geo["dx_left"], geo["dy_top"],
         geo["dx_left"] + geo["iw"] - 1, geo["dy_top"] + geo["ih"] - 1],
        fill=0)

    return canvas, mask, orig


def _border_base_mask(geo: dict) -> Image.Image:
    """Raw border mask on the WxH canvas: white in the border region,
    black (0) over the original rectangle. Acts as ThresholdMask(0.0)
    since the values are already strictly 0/255.
    """
    W, H = geo["W"], geo["H"]
    mask = Image.new("L", (W, H), 255)
    draw = ImageDraw.Draw(mask)
    draw.rectangle(
        [geo["dx_left"], geo["dy_top"],
         geo["dx_left"] + geo["iw"] - 1, geo["dy_top"] + geo["ih"] - 1],
        fill=0)
    return mask


def _blur_size_to_radius(size: int) -> float:
    """Map a node blur-size parameter to a Gaussian sigma (kernel sigma in
    comfyui-inpaint-nodes: sigma = 0.3*(size-1)+0.8)."""
    return 0.3 * (size - 1) + 0.8


def compute_mask_params(cfg: dict, W: int, H: int, orig_w: int | None = None,
                      orig_h: int | None = None) -> tuple[int, int, int]:
    """Plugin-style grow/feather/blend (model/model.py:1585-1606).

    GROW/FEATHER/BLEND = 0 (or unset) -> auto:
        feather = max(round(0.10 * selection_diagonal), 32)
        grow     = selection_grow_offset(4) + feather // 2
        blend    = min(selection_blend(25), grow + feather // 2)
    Any non-zero value in .env overrides that single parameter.

    The feather is additionally capped to ~6% of the ORIGINAL image's
    shorter side, so the denoise zone never swallows the preserved photo
    (for very large expansions the pure diagonal formula would redraw
    almost the whole original and blur it).
    """
    grow = max(0, env_int(cfg.get("GROW", "0"), 0))
    feather = max(0, env_int(cfg.get("FEATHER", "0"), 0))
    blend = max(0, env_int(cfg.get("BLEND", "0"), 0))
    if grow == 0 and feather == 0 and blend == 0:
        feather = max(round(0.10 * math.hypot(W, H)), 32)
        if orig_w and orig_h:
            cap = max(24, round(0.06 * min(orig_w, orig_h)))
            feather = min(feather, cap)
        grow = 4 + feather // 2
        blend = min(25, grow + feather // 2)
    return grow, feather, blend


def compute_pass_resolution(geo: dict, cfg: dict) -> tuple[int, int]:
    """Plugin resolution plan (backend/resolution.py prepare_diffusion_input):
    single pass at full res. Only when the canvas significantly exceeds the
    checkpoint max (max_size=2048, max_pixel_count=4MP for non-inpaint flux)
    run the plugin's 2-pass hi-res refine: initial generation at reduced res,
    upscale + refine pass at full res (strength 0.4)."""
    W, H = geo["W"], geo["H"]
    max_size = env_int(cfg.get("REFINE_MAX_LONG", "2048"), 2048)
    max_pixels = env_int(cfg.get("REFINE_MAX_PIXELS", "4000000"), 4000000)
    max_scale = math.sqrt(max_pixels / (W * H))
    if max_scale < 0.9 and (W > max_size or H > max_size):
        iw = max(DIF_MULT, int(math.ceil(W * max_scale / DIF_MULT)) * DIF_MULT)
        ih = max(DIF_MULT, int(math.ceil(H * max_scale / DIF_MULT)) * DIF_MULT)
        return iw, ih
    return W, H


def build_compositing_mask(geo: dict, grow: int, feather: int, blend: int) -> Image.Image:
    """Alpha mask for compositing the generated result over the original.

    Mirrors the plugin exactly (workflow.py:941-945 applied to the
    grown+feathered denoise mask, workflow.py:1040-1044):
        ExpandMask(border, grow, feather, linear) -> ThresholdMask(0.0)
        -> ShrinkMask(blend//2, blend)
    The threshold makes every nonzero (grown/feathered) pixel fully opaque,
    so the 0->255 transition band lies INSIDE the original photo region
    (bg = real content). The border region is forced fully opaque, so the
    generated border never blends with the black background - this removes
    the black shadow seam.

    White = use generated content, black = keep original pixels.
    """
    pad = max(feather, blend, 8) + 8
    mask = ImageOps.expand(_border_base_mask(geo), border=pad, fill=255)
    if grow > 0:
        mask = mask.filter(ImageFilter.MaxFilter(2 * grow + 1))   # binary dilation
    if feather > 0:
        r = max(1, feather // 4)  # two box blurs ~= linear (triangular) blur
        mask = mask.filter(ImageFilter.BoxBlur(r)).filter(ImageFilter.BoxBlur(r))
    mask = mask.point(lambda v: 255 if v > 0 else 0)             # ThresholdMask(0.0)
    if blend > 0:
        mask = mask.filter(ImageFilter.MinFilter(2 * (blend // 2) + 1))  # binary erosion
        mask = mask.filter(ImageFilter.GaussianBlur(_blur_size_to_radius(blend)))
    mask = mask.crop((pad, pad, pad + geo["W"], pad + geo["H"]))
    # Border region stays fully opaque (plugin guarantee: composite mask is
    # opaque inside the selection); transition only over the original photo.
    border = _border_base_mask(geo)
    mask = Image.composite(Image.new("L", (geo["W"], geo["H"]), 255), mask, border)
    return mask


def encode_png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# ComfyUI client
# ---------------------------------------------------------------------------

class ComfyError(Exception):
    pass


class ComfyClient:
    def __init__(self, base_url: str, timeout: int = 30):
        self.base = base_url.rstrip("/")
        self.timeout = timeout
        self.client_id = str(uuid.uuid4())

    # -- low level ---------------------------------------------------------
    def get(self, path: str, timeout=None):
        r = requests.get(f"{self.base}/{path}", timeout=timeout or self.timeout)
        r.raise_for_status()
        return r.json()

    def post_prompt(self, data: dict):
        r = requests.post(f"{self.base}/prompt", json=data, timeout=self.timeout)
        if r.status_code != 200:
            detail = ""
            try:
                j = r.json()
                err = j.get("error") or {}
                detail = err.get("message") or j.get("node_errors") or str(j)
            except Exception:
                detail = r.text[:2000]
            raise ComfyError(f"Prompt rejected ({r.status_code}): {detail}")
        return r.json()

    def put_image(self, image_id: str, data: bytes):
        r = requests.put(
            f"{self.base}/api/etn/image/{image_id}", data=data,
            headers={"Content-Type": "image/png"}, timeout=self.timeout)
        if r.status_code not in (200, 201):
            raise ComfyError(f"Image upload failed ({r.status_code}): {r.text[:300]}")

    def get_image(self, image_id: str) -> bytes:
        r = requests.get(f"{self.base}/api/etn/image/{image_id}", timeout=self.timeout)
        r.raise_for_status()
        return r.content

    def history(self, prompt_id: str):
        r = requests.get(f"{self.base}/history/{prompt_id}", timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def system_stats(self):
        return self.get("system_stats")

    def object_info(self):
        return self.get("object_info")

    # -- job execution -----------------------------------------------------
    def wait_job(self, prompt_id: str, timeout: float) -> dict:
        start = time.time()
        while time.time() - start < timeout:
            hist = self.history(prompt_id)
            entry = hist.get(prompt_id)
            if entry is not None:
                status = entry.get("status", {})
                s = status.get("status_str", "")
                if s in ("success", "error") or status.get("completed"):
                    return entry
            time.sleep(1.0)
        raise ComfyError(f"Job timed out after {timeout:.0f}s")


def crc32_id(data: bytes) -> str:
    return f"{zlib.crc32(data) & 0xFFFFFFFF:08x}"


# ---------------------------------------------------------------------------
# Graph builder
# ---------------------------------------------------------------------------

class Workflow:
    def __init__(self):
        self.nodes = {}
        self._n = 1

    def add(self, class_type: str, **inputs) -> str:
        node_id = str(self._n)
        self._n += 1
        self.nodes[node_id] = {"class_type": class_type, "inputs": inputs}
        return node_id

    def root(self):
        return self.nodes


def _scale_mask_nodes(w: Workflow, mask: str, width: int, height: int) -> str:
    """Scale a mask to a new resolution in-graph (plugin scale_mask:
    MaskToImage -> ImageScale bilinear -> ImageToMask)."""
    as_img = w.add("MaskToImage", mask=mask)
    scaled = w.add("ImageScale", image=[as_img, 0], width=width, height=height,
                   upscale_method="bilinear", crop="disabled")
    return w.add("ImageToMask", image=[scaled, 0], channel="red")


def _guider_nodes(w: Workflow, model: str, positive: str, negative: str,
                 cfg_scale: float) -> str:
    """Sampler guider for flux2_4b: the plugin uses CFGGuider (flux2 is
    NOT is_flux_like, so no FluxGuidance; workflow.py:340-362)."""
    if cfg_scale > 1:
        return w.add("CFGGuider", model=[model, 0],
                     positive=[positive, 0], negative=[negative, 0],
                     cfg=cfg_scale)
    return w.add("BasicGuider", model=[model, 0], conditioning=[positive, 0])


def _inpaint_pass1_nodes(w: Workflow, clip: str, vae: str, model: str,
                         pixels: str, mask: str, prompt: str,
                         cfg_scale: float, width: int, height: int,
                         steps: int, seed: int) -> tuple[str, str]:
    """Plugin pass 1 for flux2_4b (workflow.py:1068-1084, 1098-1102):
    the outpaint LoRA is registered as ControlMode.inpaint, so
    find_control(inpaint) finds it and is_inpaint_model is False -> plain
    VAEEncode + SetLatentNoiseMask (NOT concat-latent). Then
    ReferenceLatent (edit_reference=True) -> CFGGuider -> sampler.
    Returns (out_latent, sampler).
    """
    latent = w.add("VAEEncode", vae=[vae, 0], pixels=[pixels, 0])
    latent = w.add("SetLatentNoiseMask", samples=[latent, 0], mask=[mask, 0])
    pos = w.add("CLIPTextEncode", clip=[clip, 0], text=prompt)
    neg = w.add("CLIPTextEncode", clip=[clip, 0], text="")
    pos = w.add("ReferenceLatent", conditioning=[pos, 0], latent=[latent, 0])
    neg = w.add("ReferenceLatent", conditioning=[neg, 0], latent=[latent, 0])
    guider = _guider_nodes(w, model, pos, neg, cfg_scale)
    sigmas = w.add("Flux2Scheduler", steps=steps, width=width, height=height)
    sampler = w.add("KSamplerSelect", sampler_name="euler")
    noise = w.add("RandomNoise", noise_seed=seed)
    out_latent = w.add("SamplerCustomAdvanced", noise=[noise, 0],
                       guider=[guider, 0], sampler=[sampler, 0],
                       sigmas=[sigmas, 0], latent_image=[latent, 0])
    return out_latent, sampler


def build_graph(geo: dict, canvas_id: str, mask_id: str, cfg: dict, seed: int,
                initial: tuple[int, int] | None = None):
    """Build the outpainting graph mirroring the plugin's Expand workflow
    (workflow.py:1010-1156). `initial` != (W,H) enables the 2-pass hi-res
    refine: first pass at reduced resolution, upscale + refine at full res.
    """
    W, H = geo["W"], geo["H"]
    steps = max(1, env_int(cfg.get("STEPS", "50"), 50))
    cfg_scale = env_float(cfg.get("CFG", "4.0"), 4.0)
    grow, feather, blend = compute_mask_params(cfg, W, H, geo.get("iw"), geo.get("ih"))
    prompt = str(cfg.get("PROMPT") or PROMPT_FALLBACK).strip() or PROMPT_FALLBACK
    color_match = env_bool(str(cfg.get("COLOR_MATCH", "true")))

    w = Workflow()

    model = w.add("UNETLoader", unet_name=UNET_MODEL, weight_dtype="default")
    clip = w.add("CLIPLoaderGGUF", clip_name=CLIP_MODEL, type="flux2")
    vae = w.add("VAELoader", vae_name=VAE_MODEL)
    model = w.add("DifferentialDiffusion", model=[model, 0])
    # outpaint LoRA is registered as ControlMode.inpaint for flux2_4b and
    # is applied in apply_control with strength 1.0 (workflow.py:592-600,
    # resources.py:809) - not from the style loras
    model = w.add("LoraLoaderModelOnly", model=[model, 0],
                  lora_name=LORA_MODEL, strength_model=1.0)

    img = w.add("ETN_LoadImageCache", id=canvas_id)          # output 0 = image
    msk = w.add("ETN_LoadImageCache", id=mask_id)            # output 1 = mask

    # denoise mask: grow + feather + stabilize (plugin apply_grow_feather
    # + scale_to_initial + stabilize_mask, workflow.py:1040-1044)
    grown = w.add("INPAINT_ExpandMask", mask=[msk, 1],
                  grow=grow, blur=feather, blur_type="linear")
    cropped = w.add("CropMask", mask=[grown, 0],
                    x=0, y=0, width=W, height=H)
    inpaint_mask = w.add("INPAINT_StabilizeMask", mask=[cropped, 0], epsilon=0.01)

    # FillMode.green for flux2_4b Expand (plugin workflow.py:968): the
    # canvas already has the original photo on a #00FF00 border, and the
    # outpaint LoRA + "Fill the green spaces" prompt are trained on that.
    filled = img

    refine = initial is not None and initial != (W, H)

    if refine:
        # ---- pass 1 at reduced resolution (plugin workflow.py:1095-1101) --
        iw, ih = initial
        img1 = w.add("ImageScale", image=[filled, 0], width=iw, height=ih,
                     upscale_method="lanczos", crop="disabled")
        mask1 = _scale_mask_nodes(w, inpaint_mask, iw, ih)
        out_latent, sampler = _inpaint_pass1_nodes(
            w, clip, vae, model, img1, mask1, prompt, cfg_scale,
            iw, ih, steps, seed)
        decoded1 = w.add("VAEDecode", vae=[vae, 0], samples=[out_latent, 1])

        # ---- upscale + refine pass at full res (workflow.py:1103-1138) ----
        upscaler_name = str(cfg.get("REFINE_UPSCALER") or UPSCALE_MODEL)
        upscaler = w.add("UpscaleModelLoader", upscale_model=upscaler_name)
        upscaled = w.add("ImageUpscaleWithModel", upscale_model=[upscaler, 0],
                         image=[decoded1, 0])
        upscaled = w.add("ImageScale", image=[upscaled, 0], width=W, height=H,
                         upscale_method="lanczos", crop="disabled")
        latent2 = w.add("VAEEncode", vae=[vae, 0], pixels=[upscaled, 0])
        latent2 = w.add("SetLatentNoiseMask", samples=[latent2, 0],
                        mask=[inpaint_mask, 0])
        start_at = round(steps * (1.0 - REFINE_STRENGTH))
        # pass 2 has NO ReferenceLatent (plugin encode_prompt only,
        # workflow.py:1125-1137)
        pos2 = w.add("CLIPTextEncode", clip=[clip, 0], text=prompt)
        neg2 = w.add("CLIPTextEncode", clip=[clip, 0], text="")
        guider2 = _guider_nodes(w, model, pos2, neg2, cfg_scale)
        sigmas2 = w.add("Flux2Scheduler", steps=steps, width=W, height=H)
        sigmas2 = w.add("SplitSigmas", sigmas=[sigmas2, 0], step=start_at)
        noise2 = w.add("RandomNoise", noise_seed=seed)
        out_latent2 = w.add("SamplerCustomAdvanced", noise=[noise2, 0],
                            guider=[guider2, 0], sampler=[sampler, 0],
                            sigmas=[sigmas2, 1], latent_image=[latent2, 0])
        decoded = w.add("VAEDecode", vae=[vae, 0], samples=[out_latent2, 1])
        reference, exmask = filled, inpaint_mask
    else:
        # ---- single pass at full res (plugin workflow.py:1046-1098) -------
        out_latent, _ = _inpaint_pass1_nodes(
            w, clip, vae, model, filled, inpaint_mask, prompt, cfg_scale,
            W, H, steps, seed)
        decoded = w.add("VAEDecode", vae=[vae, 0], samples=[out_latent, 1])
        reference, exmask = filled, inpaint_mask

    if color_match:
        decoded = w.add("INPAINT_ColorMatch", target=[decoded, 0],
                        reference=[reference, 0], exclude_mask=[exmask, 0],
                        strength=1.0)

    w.add("ETN_SaveImageCache", images=[decoded, 0], format="PNG")
    return w


# ---------------------------------------------------------------------------
# Server management
# ---------------------------------------------------------------------------

def server_is_up(base: str) -> bool:
    try:
        requests.get(f"{base}/system_stats", timeout=3).raise_for_status()
        return True
    except Exception:
        return False


def start_server(cfg: dict, log_path: Path):
    server_dir = Path(str(cfg.get("SERVER_DIR", "")))
    py = server_dir / "venv" / "Scripts" / "python.exe"
    comfy_dir = server_dir / "ComfyUI"
    if not py.exists():
        raise ComfyError(f"Python not found: {py}")
    if not comfy_dir.exists():
        raise ComfyError(f"ComfyUI not found: {comfy_dir}")

    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = open(log_path, "a", encoding="utf-8")
    env = dict(os.environ)
    env["ONEDNN_MAX_CPU_ISA"] = "AVX2"  # plugin workaround
    print(f"Starting ComfyUI: {py} -su main.py (cwd={comfy_dir})")
    proc = __import__("subprocess").Popen(
        [str(py), "-su", "main.py"],
        cwd=str(comfy_dir), env=env,
        stdout=log_file, stderr=__import__("subprocess").STDOUT,
        creationflags=0x00000008 if os.name == "nt" else 0,  # DETACHED_PROCESS
    )
    return proc


# ---------------------------------------------------------------------------
# Main flow
# ---------------------------------------------------------------------------

def log(msg: str):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def load_results(out_dir: Path) -> dict:
    """Return {image_key: {status, ...}} from results.jsonl (legacy: manifest.jsonl)."""
    result = {}
    for name in ("manifest.jsonl", "results.jsonl"):  # legacy first, newer wins
        p = out_dir / name
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                result[entry.get("key")] = entry
            except json.JSONDecodeError:
                continue
    return result


def append_results(out_dir: Path, entry: dict):
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "results.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def collect_images(src_dir: Path) -> list[Path]:
    exts = (".png", ".jpg", ".jpeg", ".webp", ".bmp")
    files = [p for p in src_dir.iterdir() if p.suffix.lower() in exts]
    return sorted(files, key=lambda p: p.name.lower())


def verify_server(client: ComfyClient, cfg: dict):
    log(f"Verifying server at {client.base} ...")
    stats = client.system_stats()
    gpu = "?"
    try:
        dev = stats.get("devices") or []
        if dev:
            gpu = dev[0].get("name", "?")
    except Exception:
        pass
    log(f"Server OK (GPU: {gpu})")

    info = client.object_info()

    missing = [n for n in REQUIRED_NODES if n not in info]
    if missing:
        raise ComfyError("Missing ComfyUI nodes: " + ", ".join(missing))

    def options(node: str, field: str):
        try:
            spec = (info.get(node) or {}).get("input", {}).get("required", {}).get(field)
            if not spec:
                return []
            if isinstance(spec[0], list):
                return list(spec[0])
            if isinstance(spec[0], str) and spec[0].upper() == "COMBO":
                return list(spec[1].get("options") or [])
            return []
        except Exception:
            return []

    diff_models = options("UNETLoader", "unet_name")
    clip_models = options("CLIPLoaderGGUF", "clip_name")
    loras = options("LoraLoaderModelOnly", "lora_name")
    vaes = options("VAELoader", "vae_name")
    upscalers = options("UpscaleModelLoader", "model_name")

    def check(name, value, lst):
        if value not in lst:
            raise ComfyError(
                f"Model not found: {name} = {value}\n"
                f"Available: {', '.join(lst[:10])}{'...' if len(lst) > 10 else ''}")

    check("diffusion model", UNET_MODEL, diff_models)
    check("text encoder", CLIP_MODEL, clip_models)
    check("outpaint LoRA", LORA_MODEL, loras)
    check("VAE", VAE_MODEL, vaes)
    check("refine upscaler",
          str(cfg.get("REFINE_UPSCALER") or UPSCALE_MODEL), upscalers)
    log("All required nodes and models present.")


def run_outpaint_pass(client: ComfyClient, cfg: dict, name: str, source: Image.Image,
                      geo: dict, out_dir: Path, seed: int,
                      out_stem: str) -> Image.Image:
    """Run one outpainting job (plugin Expand for flux2_4b, FillMode.green):
    green canvas -> diffuser -> (optional 2-pass refine) -> composite.

    The composite mask is the plugin's denoise_to_compositing_mask built from
    the grown+feathered denoise mask, so the seam transition lies INSIDE the
    original photo (bg = real content, never black). No median post-denoise
    (the plugin has none).

    Returns the composited TWxTH image; it is also saved to out_dir.
    """
    W, H = geo["W"], geo["H"]
    grow, feather, blend = compute_mask_params(cfg, W, H, geo.get("iw"), geo.get("ih"))
    initial = compute_pass_resolution(geo, cfg)
    canvas, mask, orig = prepare_inputs(source, geo)
    canvas_bytes = encode_png(canvas)
    mask_bytes = encode_png(mask)
    canvas_id = crc32_id(canvas_bytes)
    mask_id = crc32_id(mask_bytes)

    client.put_image(canvas_id, canvas_bytes)
    client.put_image(mask_id, mask_bytes)

    prompt_id = str(uuid.uuid4())
    wf = build_graph(geo, canvas_id, mask_id, cfg, seed, initial)
    resp = client.post_prompt({
        "prompt": wf.root(),
        "client_id": client.client_id,
        "prompt_id": prompt_id,
    })
    if resp.get("prompt_id") != prompt_id:
        raise ComfyError(
            f"Prompt ID mismatch: {resp.get('prompt_id')} != {prompt_id}")
    refine = initial != (W, H)
    log(f"  submitted ({prompt_id[:8]}) seed={seed} steps={cfg.get('STEPS')} "
        f"canvas={W}x{H} out={geo['TW']}x{geo['TH']} "
        f"grow={grow} feather={feather} blend={blend}"
        + (f" refine: initial={initial[0]}x{initial[1]}" if refine else ""))

    entry = client.wait_job(prompt_id, env_int(cfg.get("TIMEOUT_PER_IMAGE", "900"), 900))
    status = entry.get("status", {})
    if status.get("status_str") == "error" or not status.get("completed"):
        msgs = status.get("messages", [])
        err = "unknown error"
        for m in msgs:
            if isinstance(m, list) and len(m) > 1 and m[0] == "execution_error":
                err = str(m[1].get("exception_message", m[1]))
        raise ComfyError(f"Execution error: {err}")

    # collect output image ids from ETN_SaveImageCache
    out_ids = []
    outputs = entry.get("outputs", {})
    for node_out in outputs.values():
        for img in node_out.get("images", []) or []:
            if img.get("source") == "http" and img.get("id"):
                out_ids.append(img["id"])
    if not out_ids:
        raise ComfyError("Job finished but no output images found in history")

    data = client.get_image(out_ids[0])
    decoded = Image.open(io.BytesIO(data)).convert("RGB")
    if decoded.size != (W, H):
        decoded = decoded.resize((W, H), Image.LANCZOS)

    # Composite background = the preserved document content (original photo
    # centered). The composite mask is opaque over the border and transitions
    # inside the original photo, so the black outside never blends in.
    bg = Image.new("RGB", (W, H), (0, 0, 0))
    bg.paste(orig, (geo["dx_left"], geo["dy_top"]))

    # Crop BEFORE composite (plugin order: crop -> composite in target bounds)
    g = geo
    box = (g["crop_x"], g["crop_y"],
           g["crop_x"] + g["TW"], g["crop_y"] + g["TH"])
    decoded_c = decoded.crop(box)
    bg_c = bg.crop(box)
    comp_mask = build_compositing_mask(geo, grow, feather, blend).crop(box)

    result = Image.composite(decoded_c, bg_c, comp_mask)

    out_path = out_dir / f"{out_stem}.png"
    out_path.write_bytes(encode_png(result))
    with Image.open(out_path) as check:
        check.verify()
        size = check.size
    if size != (g["TW"], g["TH"]):
        log(f"  WARNING: output size {size} != expected {(g['TW'], g['TH'])}")
    log(f"  OK -> {out_path}")
    return result


def process_one(client: ComfyClient, cfg: dict, image_path: Path, src_dir: Path,
                out_dir: Path, results: dict, args) -> dict:
    name = image_path.name
    key = f"{name}|{cfg.get('ASPECT_RATIO', 'screen')}|{PIPE_VERSION}"

    if args.resume and results.get(key, {}).get("status") == "ok":
        log(f"SKIP (already done): {name}")
        return {"key": key, "status": "skipped", "image": name}

    try:
        im = Image.open(image_path)
        iw, ih = im.size
    except Exception as e:
        log(f"ERROR reading {name}: {e}")
        return {"key": key, "status": "error", "image": name, "error": str(e)}

    try:
        target_w, target_h = parse_aspect_ratio(cfg)
        max_long = env_int(cfg.get("MAX_LONG_SIDE", "2560"), 2560)
        max_pixels = env_int(cfg.get("MAX_PIXELS", "6000000"), 6000000)
        final_geo = compute_geometry(iw, ih, target_w, target_h,
                                     max_long, max_pixels)
    except Exception as e:
        log(f"ERROR geometry {name}: {e}")
        return {"key": key, "status": "error", "image": name, "error": str(e)}

    if final_geo is None:
        log(f"SKIP (ratio already matches): {name} ({iw}x{ih})")
        if args.resume:
            return {"key": key, "status": "ok", "image": name,
                    "reason": "ratio_match", "src": iw, "ih": ih}
        return {"key": key, "status": "skipped", "image": name, "reason": "ratio_match"}

    log(f"Process {name}: {iw}x{ih} -> {final_geo['TW']}x{final_geo['TH']} "
        f"(L+{final_geo['dx_left']} R+{final_geo['dx_right']} "
        f"T+{final_geo['dy_top']} B+{final_geo['dy_bottom']})")

    if args.dry_run:
        seed = args.seed if args.seed is not None else 0
        canvas, mask, _ = prepare_inputs(im, final_geo)
        canvas_bytes = encode_png(canvas)
        mask_bytes = encode_png(mask)
        canvas_id = crc32_id(canvas_bytes)
        mask_id = crc32_id(mask_bytes)
        wf = build_graph(final_geo, canvas_id, mask_id, cfg, seed,
                         compute_pass_resolution(final_geo, cfg))
        debug_dir = out_dir / "debug"
        debug_dir.mkdir(parents=True, exist_ok=True)
        dump = {"key": key, "geometry": final_geo, "graph": wf.root()}
        (debug_dir / f"{Path(name).stem}.graph.json").write_text(
            json.dumps(dump, indent=2), encoding="utf-8")
        log(f"DRY-RUN: graph written to {debug_dir / (Path(name).stem + '.graph.json')}")
        return {"key": key, "status": "dry-run", "image": name}

    try:
        seed = args.seed if args.seed is not None else (
            env_int(cfg.get("SEED", "-1"), -1))
        if seed < 0:
            seed = random.randint(0, 2 ** 31 - 1)

        stem = Path(name).stem
        source = ImageOps.exif_transpose(im)
        if source.mode != "RGB":
            source = source.convert("RGB")

        # Single pass (plugin scheme). A 2-pass hi-res refine runs inside the
        # graph only when the canvas exceeds the model max context.
        result = run_outpaint_pass(
            client, cfg, name, source, final_geo, out_dir, seed,
            out_stem=f"{stem}__{final_geo['TW']}x{final_geo['TH']}")
        out_path = out_dir / f"{stem}__{final_geo['TW']}x{final_geo['TH']}.png"
        grow, feather, blend = compute_mask_params(
            cfg, final_geo["W"], final_geo["H"],
            final_geo.get("iw"), final_geo.get("ih"))
        initial = compute_pass_resolution(final_geo, cfg)
        return {"key": key, "status": "ok", "image": name,
                "output": str(out_path),
                "size": [final_geo["TW"], final_geo["TH"]],
                "seed": seed, "src_size": [iw, ih],
                "border": final_geo["border"], "passes": 1,
                "refine": initial != (final_geo["W"], final_geo["H"]),
                "grow": grow, "feather": feather, "blend": blend,
                "ratio": cfg.get("ASPECT_RATIO", "screen")}
    except Exception as e:
        log(f"ERROR {name}: {e}")
        return {"key": key, "status": "error", "image": name, "error": str(e)}


def main():
    ap = argparse.ArgumentParser(description="Batch outpainting to target aspect ratio")
    ap.add_argument("--env", default=".env", help="path to .env")
    ap.add_argument("--src", help="override source directory")
    ap.add_argument("--out", help="override output directory")
    ap.add_argument("--ratio", help="override aspect ratio (screen|WxH|float)")
    ap.add_argument("--steps", type=int, help="override steps")
    ap.add_argument("--cfg", type=float, help="override CFG")
    ap.add_argument("--seed", type=int, default=None, help="fixed seed")
    ap.add_argument("--limit", type=int, default=0, help="max images to process (0=all)")
    ap.add_argument("--only", help="process only files containing this substring")
    ap.add_argument("--resume", action="store_true", help="resume (skip done)")
    ap.add_argument("--no-resume", action="store_true", help="ignore results file")
    ap.add_argument("--dry-run", action="store_true",
                    help="dump graph JSON instead of submitting")
    ap.add_argument("--check", action="store_true",
                    help="only verify server/nodes/models and exit")
    args = ap.parse_args()

    env_path = Path(args.env)
    cfg = parse_env(env_path)
    if args.ratio:
        cfg["ASPECT_RATIO"] = args.ratio
    if args.steps:
        cfg["STEPS"] = str(args.steps)
    if args.cfg:
        cfg["CFG"] = str(args.cfg)

    src_dir = Path(args.src or cfg["SRC_DIR"])
    out_dir = Path(args.out or cfg["OUT_DIR"])
    if not src_dir.is_absolute():
        src_dir = Path.cwd() / src_dir
    if not out_dir.is_absolute():
        out_dir = Path.cwd() / out_dir
    if not src_dir.exists():
        log(f"ERROR: source directory not found: {src_dir}")
        sys.exit(2)
    out_dir.mkdir(parents=True, exist_ok=True)

    host = cfg.get("SERVER_URL", "127.0.0.1:8188")
    base = f"http://{host}"
    client = ComfyClient(base)

    if not args.dry_run:
        if not server_is_up(base):
            proc = start_server(cfg, out_dir / "logs" / "comfyui.log")
            log(f"Waiting for ComfyUI at {base} ...")
            deadline = time.time() + 180
            while not server_is_up(base):
                if time.time() > deadline:
                    if proc is not None:
                        proc.kill()
                    raise ComfyError("ComfyUI did not start within 180s (see logs/comfyui.log)")
                time.sleep(2)
        else:
            log(f"Reusing running ComfyUI at {base}")

        try:
            verify_server(client, cfg)
        except ComfyError as e:
            log(f"ERROR: {e}")
            sys.exit(3)

        if args.check:
            log("Check OK.")
            return
    elif args.check:
        log("ERROR: --check requires a running server (cannot combine with --dry-run)")
        sys.exit(2)

    resume = args.resume or (not args.no_resume and env_bool(str(cfg.get("RESUME", "true"))))
    args.resume = resume  # make env-based RESUME visible to process_one()
    results = load_results(out_dir) if resume else {}

    images = collect_images(src_dir)
    if not images:
        log(f"No images found in {src_dir}")
        return
    if args.only:
        images = [p for p in images if args.only.lower() in p.name.lower()]
    if args.limit > 0:
        images = images[: args.limit]
    log(f"Found {len(images)} image(s) in {src_dir}")

    stats = {"ok": 0, "error": 0, "skipped": 0}
    t0 = time.time()
    for img in images:
        res = process_one(client, cfg, img, src_dir, out_dir, results, args)
        # --dry-run must NOT touch the results file, otherwise its "dry-run"
        # entries overwrite previously recorded "ok" status and break resume.
        if not args.dry_run:
            append_results(out_dir, {**res, "ts": time.time()})
        stats[res.get("status", "skipped")] = stats.get(res.get("status", "skipped"), 0) + 1

    log(f"Done in {time.time() - t0:.1f}s. "
        f"ok={stats['ok']} error={stats['error']} skipped={stats['skipped']}")
    if stats["error"] > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
