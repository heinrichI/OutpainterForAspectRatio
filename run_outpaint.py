#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Batch outpainting to a target aspect ratio using the krita-ai-diffusion
backend (ComfyUI + FLUX.2 Klein 4B fp8 + fal outpaint LoRA).

Mirrors the plugin's "Expand" workflow:
    green fill (#00FF00) -> VAEEncode -> SetLatentNoiseMask
    -> ReferenceLatent -> FluxGuidance -> Flux2Scheduler/euler
    -> SamplerCustomAdvanced -> VAEDecode -> ETN_SaveImageCache

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
    "GROW": "16",
    "FEATHER": "8",
    "BLEND": "25",
    "LORA_STRENGTH": "1.1",
    "BORDER_MAX_RATIO": "0.25",
    "SKIP_LARGE_BORDER": "false",
    "SKIP_IF_MATCH": "true",
    "COLOR_MATCH": "true",
    "MULTI_PASS": "false",
    "PASS_BORDER_RATIO": "0.25",
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

REQUIRED_NODES = [
    "UNETLoader", "LoraLoaderModelOnly", "CLIPLoaderGGUF", "VAELoader",
    "DifferentialDiffusion", "ETN_LoadImageCache", "INPAINT_ExpandMask",
    "CropMask", "INPAINT_StabilizeMask", "ThresholdMask", "EmptyImage",
    "ImageCompositeMasked", "VAEEncode", "SetLatentNoiseMask",
    "CLIPTextEncode", "ReferenceLatent", "FluxGuidance", "BasicGuider",
    "Flux2Scheduler", "KSamplerSelect", "RandomNoise", "SamplerCustomAdvanced",
    "VAEDecode", "ETN_SaveImageCache", "INPAINT_ColorMatch",
]

DIF_MULT = 16  # diffusion multiple
PIPE_VERSION = "v3"  # bump to force reprocessing after pipeline changes


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


def compute_step_geometry(cur_w: int, cur_h: int, target_w: int, target_h: int,
                          max_border: int, max_long: int, max_pixels: int,
                          tol: float = 0.01):
    """Compute the geometry for one outpainting *step* in a multi-pass run.

    Like compute_geometry but the border added per side is capped at
    `max_border` pixels, so large expansions are split into several passes
    of <= PASS_BORDER_RATIO of the current min side (the LoRA's sweet spot).
    Returns None when the target ratio is already reached.
    """
    in_ratio = cur_w / cur_h
    out_ratio = target_w / target_h
    if abs(out_ratio - in_ratio) <= tol:
        return None
    if out_ratio > in_ratio:
        ideal_w = cur_h * out_ratio
        ideal_h = cur_h
    else:
        ideal_w = cur_w
        ideal_h = cur_w / out_ratio
    need_w = max(0.0, (ideal_w - cur_w) / 2.0)
    need_h = max(0.0, (ideal_h - cur_h) / 2.0)
    step_w = min(need_w, float(max_border))
    step_h = min(need_h, float(max_border))
    tgt_w = cur_w + 2 * step_w
    tgt_h = cur_h + 2 * step_h
    return compute_geometry(cur_w, cur_h, tgt_w, tgt_h, max_long, max_pixels, tol)


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


def build_compositing_mask(geo: dict, blend: int) -> Image.Image:
    """Alpha mask for compositing the generated result over the original.

    Mirrors the plugin's `denoise_to_compositing_mask`
    (workflow.py:941-945):
        ThresholdMask(0.0) -> ShrinkMask(blend//2, blend)
    i.e. the white border area is eroded by blend//2 towards the generated
    side (MinFilter), then blurred by blend (Gaussian). The mask therefore
    transitions *into* the outpainted border, never over the original,
    which removes the thin green line at the seam.

    White = use generated content, black = keep original pixels.
    """
    mask = _border_base_mask(geo)
    if blend > 0:
        mask = mask.filter(ImageFilter.MinFilter(2 * (blend // 2) + 1))
        mask = mask.filter(ImageFilter.GaussianBlur(blend))
    return mask


def build_hard_border_mask(geo: dict) -> Image.Image:
    """Hard (0/255, unfeathered) mask of the outpainted border region on the
    WxH canvas. Used for post-denoising so the original stays bit-exact.
    """
    return _border_base_mask(geo)


def denoise_border(img: Image.Image, hard_mask: Image.Image) -> Image.Image:
    """Median-filter the generated border only; original pixels (hard_mask=0)
    are kept bit-for-bit identical. Falls back to a no-op if cv2 is absent;
    PIL MedianFilter(3) is the default (fast, no extra deps).
    """
    denoised = img.filter(ImageFilter.MedianFilter(3))
    return Image.composite(denoised, img, hard_mask)


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


def build_graph(geo: dict, canvas_id: str, mask_id: str, cfg: dict, seed: int):
    W, H = geo["W"], geo["H"]
    steps = max(1, env_int(cfg.get("STEPS", "50"), 50))
    cfg_scale = env_float(cfg.get("CFG", "4.0"), 4.0)
    grow = max(0, env_int(cfg.get("GROW", "16"), 16))
    feather = max(0, env_int(cfg.get("FEATHER", "8"), 8))
    lora_strength = env_float(cfg.get("LORA_STRENGTH", "1.1"), 1.1)
    prompt = str(cfg.get("PROMPT") or PROMPT_FALLBACK).strip() or PROMPT_FALLBACK
    color_match = env_bool(str(cfg.get("COLOR_MATCH", "false")))

    w = Workflow()

    model = w.add("UNETLoader", unet_name=UNET_MODEL, weight_dtype="default")
    model = w.add("LoraLoaderModelOnly", model=[model, 0],
                  lora_name=LORA_MODEL, strength_model=lora_strength)
    clip = w.add("CLIPLoaderGGUF", clip_name=CLIP_MODEL, type="flux2")
    vae = w.add("VAELoader", vae_name=VAE_MODEL)
    model = w.add("DifferentialDiffusion", model=[model, 0])

    img = w.add("ETN_LoadImageCache", id=canvas_id)          # output 0 = image
    msk = w.add("ETN_LoadImageCache", id=mask_id)            # output 1 = mask

    # denoise mask: grow + feather + stabilize (plugin apply_grow_feather)
    grown = w.add("INPAINT_ExpandMask", mask=[msk, 1],
                  grow=grow, blur=feather, blur_type="linear")
    cropped = w.add("CropMask", mask=[grown, 0],
                    x=0, y=0, width=W, height=H)
    inpaint_mask = w.add("INPAINT_StabilizeMask", mask=[cropped, 0], epsilon=0.01)

    # green fill mask (plugin apply_grow + ThresholdMask(0.99))
    fill_grow = max(0, grow - feather // 2)
    fill = w.add("INPAINT_ExpandMask", mask=[msk, 1],
                 grow=fill_grow, blur=0, blur_type="linear")
    fill_mask = w.add("ThresholdMask", mask=[fill, 0], value=0.99)

    green = w.add("EmptyImage", width=W, height=H, color=65280, batch_size=1)
    canvas = w.add("ImageCompositeMasked", source=[green, 0],
                   destination=[img, 0], mask=[fill_mask, 0],
                   x=0, y=0, resize_source=False)

    latent = w.add("VAEEncode", vae=[vae, 0], pixels=[canvas, 0])
    latent = w.add("SetLatentNoiseMask", samples=[latent, 0], mask=[inpaint_mask, 0])

    pos = w.add("CLIPTextEncode", clip=[clip, 0], text=prompt)
    neg = w.add("CLIPTextEncode", clip=[clip, 0], text="")
    pos = w.add("ReferenceLatent", conditioning=[pos, 0], latent=[latent, 0])
    neg = w.add("ReferenceLatent", conditioning=[neg, 0], latent=[latent, 0])

    guidance = cfg_scale if cfg_scale > 1 else 3.5
    pos = w.add("FluxGuidance", conditioning=[pos, 0], guidance=guidance)
    guider = w.add("BasicGuider", model=[model, 0], conditioning=[pos, 0])

    sigmas = w.add("Flux2Scheduler", steps=steps, width=W, height=H)
    sampler = w.add("KSamplerSelect", sampler_name="euler")
    noise = w.add("RandomNoise", noise_seed=seed)

    out_latent = w.add("SamplerCustomAdvanced", noise=[noise, 0],
                       guider=[guider, 0], sampler=[sampler, 0],
                       sigmas=[sigmas, 0], latent_image=[latent, 0])
    decoded = w.add("VAEDecode", vae=[vae, 0], samples=[out_latent, 1])

    if color_match:
        decoded = w.add("INPAINT_ColorMatch", target=[decoded, 0],
                        reference=[canvas, 0], exclude_mask=[inpaint_mask, 0],
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


def load_manifest(out_dir: Path) -> dict:
    """Return {image_key: {status, ...}}."""
    result = {}
    p = out_dir / "manifest.jsonl"
    if not p.exists():
        return result
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


def append_manifest(out_dir: Path, entry: dict):
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "manifest.jsonl", "a", encoding="utf-8") as f:
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
            return list((info.get(node) or {}).get("input", {}).get("required", {}).get(field, [None])[0] or [])
        except Exception:
            return []

    diff_models = options("UNETLoader", "unet_name")
    clip_models = options("CLIPLoaderGGUF", "clip_name")
    loras = options("LoraLoaderModelOnly", "lora_name")
    vaes = options("VAELoader", "vae_name")

    def check(name, value, lst):
        if value not in lst:
            raise ComfyError(
                f"Model not found: {name} = {value}\n"
                f"Available: {', '.join(lst[:10])}{'...' if len(lst) > 10 else ''}")

    check("diffusion model", UNET_MODEL, diff_models)
    check("text encoder", CLIP_MODEL, clip_models)
    check("outpaint LoRA", LORA_MODEL, loras)
    check("VAE", VAE_MODEL, vaes)
    log("All required nodes and models present.")


def run_outpaint_pass(client: ComfyClient, cfg: dict, name: str, source: Image.Image,
                      geo: dict, out_dir: Path, seed: int,
                      out_stem: str) -> Image.Image:
    """Run one outpainting pass: green canvas -> diffuser -> composite.

    `source` is the current canvas state (the original image for the first
    pass, the previous pass result for later passes). Mirrors the plugin's
    order: crop first, then composite in target bounds (crop -> composite),
    which removes the cut-off feather zone and leftover green at the frame
    edge. The composite mask is built plugin-style (ThresholdMask(0.0) ->
    ShrinkMask(blend//2, blend)) so the seam transitions *into* the border,
    never over the original. Finally the generated border is median-denoised
    (original centre stays bit-exact).

    Returns the composited TWxTH image; it is also saved to out_dir.
    """
    W, H = geo["W"], geo["H"]
    canvas, mask, orig = prepare_inputs(source, geo)
    canvas_bytes = encode_png(canvas)
    mask_bytes = encode_png(mask)
    canvas_id = crc32_id(canvas_bytes)
    mask_id = crc32_id(mask_bytes)

    client.put_image(canvas_id, canvas_bytes)
    client.put_image(mask_id, mask_bytes)

    prompt_id = str(uuid.uuid4())
    wf = build_graph(geo, canvas_id, mask_id, cfg, seed)
    resp = client.post_prompt({
        "prompt": wf.root(),
        "client_id": client.client_id,
        "prompt_id": prompt_id,
    })
    if resp.get("prompt_id") != prompt_id:
        raise ComfyError(
            f"Prompt ID mismatch: {resp.get('prompt_id')} != {prompt_id}")
    log(f"  submitted ({prompt_id[:8]}) seed={seed} steps={cfg.get('STEPS')} "
        f"canvas={W}x{H} out={geo['TW']}x{geo['TH']}")

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

    # Composite background = the preserved content (source), NOT the green
    # fill. The green canvas is only the diffusion input; compositing over it
    # would bleed #00FF00 into the feathered seam. The plugin composites over
    # the actual document content, so in the blend zone generated pixels mix
    # with the real photo edge (see INPAINT_ColorMatch doc / document pass).
    bg = Image.new("RGB", (W, H), (0, 0, 0))
    bg.paste(orig, (geo["dx_left"], geo["dy_top"]))

    # Crop BEFORE composite (plugin order: crop -> composite in target bounds)
    g = geo
    box = (g["crop_x"], g["crop_y"],
           g["crop_x"] + g["TW"], g["crop_y"] + g["TH"])
    decoded_c = decoded.crop(box)
    bg_c = bg.crop(box)
    blend = max(0, env_int(cfg.get("BLEND", "25"), 25))
    comp_mask = build_compositing_mask(geo, blend).crop(box)
    hard_mask = build_hard_border_mask(geo).crop(box)

    result = Image.composite(decoded_c, bg_c, comp_mask)
    result = denoise_border(result, hard_mask)

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
                out_dir: Path, manifest: dict, args) -> dict:
    name = image_path.name
    key = f"{name}|{cfg.get('ASPECT_RATIO', 'screen')}|{PIPE_VERSION}"

    if args.resume and manifest.get(key, {}).get("status") == "ok":
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

    multi = env_bool(str(cfg.get("MULTI_PASS", "false")))
    pass_ratio = env_float(cfg.get("PASS_BORDER_RATIO", "0.25"), 0.25)

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
        wf = build_graph(final_geo, canvas_id, mask_id, cfg, seed)
        debug_dir = out_dir / "debug"
        debug_dir.mkdir(parents=True, exist_ok=True)
        dump = {"key": key, "geometry": final_geo, "graph": wf.root(),
                "multi_pass": multi}
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

        # Single pass: only used when multi-pass is disabled or the border is
        # already within the LoRA's sweet spot (<= PASS_BORDER_RATIO).
        if not multi or final_geo["border_ratio"] <= pass_ratio:
            result = run_outpaint_pass(
                client, cfg, name, source, final_geo, out_dir, seed,
                out_stem=f"{stem}__{final_geo['TW']}x{final_geo['TH']}")
            out_path = out_dir / f"{stem}__{final_geo['TW']}x{final_geo['TH']}.png"
            return {"key": key, "status": "ok", "image": name,
                    "output": str(out_path),
                    "size": [final_geo["TW"], final_geo["TH"]],
                    "seed": seed, "src_size": [iw, ih],
                    "border": final_geo["border"], "passes": 1,
                    "ratio": cfg.get("ASPECT_RATIO", "screen")}

        # Multi-pass: extend in steps of <= PASS_BORDER_RATIO of the current
        # min side. Each step preserves the previous content bit-exactly
        # (the composite background is the previous result); only the freshly
        # generated ring is composited and denoised.
        passes = 0
        cur = source
        cur_w, cur_h = iw, ih
        target_ratio = target_w / target_h
        while True:
            passes += 1
            max_border = max(1, int(round(pass_ratio * min(cur_w, cur_h))))
            step_geo = compute_step_geometry(
                cur_w, cur_h, target_w, target_h,
                max_border=max_border, max_long=max_long, max_pixels=max_pixels)
            if step_geo is None:
                break
            # NOTE: do NOT swap in final_geo here. final_geo is computed from
            # the ORIGINAL image size; each pass needs geometry derived from
            # the CURRENT canvas (the previous pass result). compute_step_geometry
            # already lands exactly on the final target size in the last step.
            is_last = (step_geo["TW"] >= final_geo["TW"] - 1
                       and step_geo["TH"] >= final_geo["TH"] - 1)
            out_stem = f"{stem}__{step_geo['TW']}x{step_geo['TH']}"
            if not is_last:
                out_stem += f"_pass{passes}"
            log(f"  pass {passes}: {cur_w}x{cur_h} -> {step_geo['TW']}x{step_geo['TH']} "
                f"(border <= {max_border}px/side)")
            cur = run_outpaint_pass(
                client, cfg, name, cur, step_geo, out_dir, seed, out_stem=out_stem)
            cur_w, cur_h = cur.size
            if (cur_w >= final_geo["TW"] - 1 and cur_h >= final_geo["TH"] - 1
                    or abs(cur_w / cur_h - target_ratio) <= 0.01):
                break

        out_path = out_dir / f"{stem}__{final_geo['TW']}x{final_geo['TH']}.png"
        return {"key": key, "status": "ok", "image": name,
                "output": str(out_path),
                "size": [final_geo["TW"], final_geo["TH"]],
                "seed": seed, "src_size": [iw, ih],
                "border": final_geo["border"], "passes": passes,
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
    ap.add_argument("--no-resume", action="store_true", help="ignore manifest")
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
    manifest = load_manifest(out_dir) if resume else {}

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
        res = process_one(client, cfg, img, src_dir, out_dir, manifest, args)
        append_manifest(out_dir, {**res, "ts": time.time()})
        stats[res.get("status", "skipped")] = stats.get(res.get("status", "skipped"), 0) + 1

    log(f"Done in {time.time() - t0:.1f}s. "
        f"ok={stats['ok']} error={stats['error']} skipped={stats['skipped']}")
    if stats["error"] > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
