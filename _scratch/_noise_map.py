import sys
sys.path.insert(0, r"f:/E/SourcePython3/OutpainterForAspectRatio2")
import run_outpaint as ro
from PIL import Image, ImageFilter
import numpy as np

d = r"f:/E/SourcePython3/OutpainterForAspectRatio2"
out = r"f:/E/SourcePython3/OutpainterForAspectRatio2/output/490005518_18504575899014999_5005429624106475873_n__2400x1350.png"
im = Image.open(out).convert("RGB")
a = np.asarray(im, dtype=np.float32)
h, w = a.shape[:2]

def band(x0, x1, y0, y1):
    r = a[y0:y1, x0:x1]
    smooth = np.asarray(Image.fromarray(r.astype(np.uint8)).filter(ImageFilter.GaussianBlur(1.0)), dtype=np.float32)
    resid = r - smooth
    return float(np.abs(resid).mean()), float(resid.std()), float(r.mean())

print("зоны (x0-x1): |HF mean|HF std|mean color")
for name, x0, x1 in [("левый край 0-100", 0, 100),
                      ("рамка 100-600", 100, 600),
                      ("переход 600-700", 600, 700),
                      ("внутри фото 700-900", 700, 900),
                      ("центр 900-1500", 900, 1500),
                      ("правая рамка 1800-2400", 1800, 2400)]:
    m, s, mean = band(x0, x1, 400, 900)
    print(f"{name:24s}: {m:6.2f} {s:6.2f} {mean:7.1f}")

# сравнить с исходником (те же зоны по фото)
src = Image.open(r"f:/E/SourcePython3/OutpainterForAspectRatio2/testImages/490005518_18504575899014999_5005429624106475873_n.jpg").convert("RGB")
sa = np.asarray(src, dtype=np.float32)
# фото 1080x1350 в канве на x=660
smooth = np.asarray(Image.fromarray(sa.astype(np.uint8)).filter(ImageFilter.GaussianBlur(1.0)), dtype=np.float32)
resid = sa - smooth
print("\nисходник: |HF mean|HF std")
for name, x0, x1 in [("левый край 0-100", 0, 100), ("центр 400-700", 400, 700)]:
    r = resid[400:900, x0:x1]
    print(f"{name:24s}: {np.abs(r).mean():6.2f} {r.std():6.2f}")
