import sys
sys.path.insert(0, r"f:/E/SourcePython3/OutpainterForAspectRatio2")
import io
import os
import run_outpaint as ro
from PIL import Image, ImageFilter
import numpy as np

base = "http://127.0.0.1:8189"
client = ro.ComfyClient(base)
out_dir = r"f:/E/SourcePython3/OutpainterForAspectRatio2/output"

jobs = {
    "50steps_cfg4": ("c5e07c5b-9eb3-4de0-a52f-3ef86478b88c", "d52422cf6419481f88c4ef99864365b2"),
    "20steps_cfg5": ("56e55dde-6d9c-4c6f-9c99-bf412e2a1fd1", "b5c8944669c94a578bd6b10dc809b2e1"),
    "concat(старый шумный)": ("63a8f7f4-83f4-4fa2-92cb-ae2a5ffa03a2", None),
}

def fetch(prompt_id):
    hist = client.history(prompt_id)
    entry = hist.get(prompt_id, {})
    out_ids = []
    for node_out in entry.get("outputs", {}).values():
        for img in node_out.get("images", []) or []:
            if img.get("source") == "http" and img.get("id"):
                out_ids.append(img["id"])
    if not out_ids:
        return None
    return client.get_image(out_ids[0])

def lap_var(im):
    g = np.asarray(im.convert("L"), dtype=np.float32)
    lap = np.asarray(Image.fromarray(g.astype(np.uint8)).filter(ImageFilter.FIND_EDGES), dtype=np.float32)
    return float(lap.var())

def hf_noise(im):
    g = np.asarray(im.convert("L"), dtype=np.float32)
    smooth = np.asarray(Image.fromarray(g.astype(np.uint8)).filter(ImageFilter.GaussianBlur(1.2)), dtype=np.float32)
    resid = g - smooth
    return float(np.abs(resid).mean()), float(resid.std())

for name, (pid, _) in jobs.items():
    data = fetch(pid)
    if data is None:
        print(f"{name}: no output in history")
        continue
    im = Image.open(io.BytesIO(data)).convert("RGB")
    # border band
    border = im.crop((0, 300, 600, 900))
    center = im.crop((900, 300, 1300, 900))
    print(f"{name:24s} size={im.size} border_sharp={lap_var(border):.0f} "
          f"border_HF={hf_noise(border)[0]:.2f}/{hf_noise(border)[1]:.2f} "
          f"center_sharp={lap_var(center):.0f}")
