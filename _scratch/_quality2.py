import sys
sys.path.insert(0, r"f:/E/SourcePython3/OutpainterForAspectRatio2")
import run_outpaint as ro
from PIL import Image, ImageFilter, ImageOps
import numpy as np
import os

d = r"f:/E/SourcePython3/OutpainterForAspectRatio2"
src_dir = os.path.join(d, "testImages")
out_dir = os.path.join(d, "output")

def lap_var(im):
    g = np.asarray(im.convert("L"), dtype=np.float32)
    lap = np.asarray(Image.fromarray(g.astype(np.uint8)).filter(ImageFilter.FIND_EDGES), dtype=np.float32)
    return float(lap.var())

def highfreq_noise(im):
    g = np.asarray(im.convert("L"), dtype=np.float32)
    smooth = np.asarray(Image.fromarray(g.astype(np.uint8)).filter(ImageFilter.GaussianBlur(1.0)), dtype=np.float32)
    resid = g - smooth
    return float(np.abs(resid).mean()), float(resid.std())

for f in ["490005518_18504575899014999_5005429624106475873_n.jpg",
          "test_portrait.png", "test_square.png"]:
    stem = os.path.splitext(f)[0]
    src_path = os.path.join(src_dir, f)
    outs = [p for p in os.listdir(out_dir) if p.startswith(stem) and p.endswith(".png")]
    if not outs:
        print(f"{f}: NO OUTPUT")
        continue
    out_path = os.path.join(out_dir, outs[0])
    src = Image.open(src_path).convert("RGB")
    out = Image.open(out_path).convert("RGB")
    print(f"\n=== {f}  src={src.size} out={out.size}")

    iw, ih = src.size
    geo = ro.compute_geometry(iw, ih, 1920, 1080, 2560, 6000000)
    W, H = geo["W"], geo["H"]
    dx, dy = geo["dx_left"], geo["dy_top"]
    nw, nh = geo["iw"], geo["ih"]
    box = (geo["crop_x"], geo["crop_y"], geo["crop_x"] + geo["TW"], geo["crop_y"] + geo["TH"])
    out_c = out.crop(box)
    ox = dx - geo["crop_x"]
    oy = dy - geo["crop_y"]
    orig_in_out = out_c.crop((ox, oy, ox + nw, oy + nh)).resize(src.size, Image.LANCZOS)

    cw, ch = int(nw * 0.3), int(nh * 0.3)
    cxs, cys = (iw - cw) // 2, (ih - ch) // 2
    cxo, cyo = (nw - cw) // 2, (nh - ch) // 2
    a_src = np.asarray(src.crop((cxs, cys, cxs + cw, cys + ch)), dtype=np.float32)
    a_out = np.asarray(orig_in_out.crop((cxo, cyo, cxo + cw, cyo + ch)), dtype=np.float32)
    diff = np.abs(a_src - a_out).mean()
    print(f"  center diff: {diff:.2f}")
    print(f"  sharp src-center: {lap_var(src.crop((cxs, cys, cxs + cw, cys + ch))):.1f}")
    print(f"  sharp out-center: {lap_var(orig_in_out.crop((cxo, cyo, cxo + cw, cyo + ch))):.1f}")
    bw, bh = int(nw * 0.15), int(nh * 0.3)
    bxo, byo = 0, cyo
    border_region = out_c.crop((bxo, byo, bxo + bw, byo + bh))
    src_border = src.crop((0, cys, int(iw*0.15), cys + ch))
    print(f"  sharp out-border(left): {lap_var(border_region):.1f}")
    hf_mean, hf_std = highfreq_noise(border_region)
    hf_src_mean, hf_src_std = highfreq_noise(src_border)
    print(f"  HF-noise border: mean={hf_mean:.2f} std={hf_std:.2f}  (src: mean={hf_src_mean:.2f} std={hf_src_std:.2f})")
