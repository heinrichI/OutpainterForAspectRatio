import sys
sys.path.insert(0, r"f:/E/SourcePython3/OutpainterForAspectRatio2")
import io
import os
import run_outpaint as ro
from PIL import Image

base = "http://127.0.0.1:8189"
client = ro.ComfyClient(base)
prompt_id = "c5e07c5b-9eb3-4de0-a52f-3ef86478b88c"

hist = client.history(prompt_id)
entry = hist.get(prompt_id)
status = entry.get("status", {})
print("status:", status.get("status_str"), "completed:", status.get("completed"))

out_ids = []
outputs = entry.get("outputs", {})
for node_out in outputs.values():
    for img in node_out.get("images", []) or []:
        if img.get("source") == "http" and img.get("id"):
            out_ids.append(img["id"])
print("output ids:", out_ids)
if not out_ids:
    raise SystemExit("no outputs")

data = client.get_image(out_ids[0])
decoded = Image.open(io.BytesIO(data)).convert("RGB")
print("decoded size:", decoded.size)

# geometry as in the run
iw, ih = 1080, 1350
geo = ro.compute_geometry(iw, ih, 1920, 1080, 2560, 6000000)
W, H = geo["W"], geo["H"]
print("geo:", {k: geo[k] for k in ("W", "H", "TW", "TH", "crop_x", "crop_y", "dx_left", "dy_top")})

source = Image.open(r"f:/E/SourcePython3/OutpainterForAspectRatio2/testImages/490005518_18504575899014999_5005429624106475873_n.jpg").convert("RGB")
canvas, mask, orig = ro.prepare_inputs(source, geo)
grow, feather, blend = ro.compute_mask_params(dict(ro.DEFAULTS), W, H, geo["iw"], geo["ih"])
print("mask params:", grow, feather, blend)

if decoded.size != (W, H):
    decoded = decoded.resize((W, H), Image.LANCZOS)

bg = Image.new("RGB", (W, H), (0, 0, 0))
bg.paste(orig, (geo["dx_left"], geo["dy_top"]))

box = (geo["crop_x"], geo["crop_y"], geo["crop_x"] + geo["TW"], geo["crop_y"] + geo["TH"])
decoded_c = decoded.crop(box)
bg_c = bg.crop(box)
comp_mask = ro.build_compositing_mask(geo, grow, feather, blend).crop(box)

result = Image.composite(decoded_c, bg_c, comp_mask)
out_path = r"f:/E/SourcePython3/OutpainterForAspectRatio2/output/490005518_18504575899014999_5005429624106475873_n__2400x1350.png"
result.save(out_path)
print("saved:", out_path, result.size)
