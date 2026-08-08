import os
import subprocess
import sys
import time

d = os.path.dirname(__file__)
out = os.path.join(d, "output")
os.makedirs(out, exist_ok=True)
for f in os.listdir(out):
    p = os.path.join(out, f)
    if os.path.isfile(p) and not f.endswith(".log") and f != "log.txt" and not f.startswith("_preview"):
        try:
            os.remove(p)
        except Exception:
            pass

script = os.path.join(d, "run_outpaint.py")
env_file = os.path.join(d, ".env.8189")
src = os.path.join(d, "testImages")
log_path = os.path.join(out, "run.log")

cmd = [
    sys.executable, script,
    "--env", env_file,
    "--src", src,
    "--out", out,
    "--ratio", "16x9",
    "--seed", "42",
    "--limit", "0",
    "--no-resume",
]

with open(log_path, "w") as log:
    proc = subprocess.Popen(
        cmd, stdout=log, stderr=subprocess.STDOUT,
        creationflags=0x00000008,
    )
    pid = proc.pid

print("BG PID:", pid)

expected = [
    "490005518_18504575899014999_5005429624106475873_n__2400x1350.png",
    "test_portrait__1365x768.png",
    "test_square__1820x1024.png",
]
deadline = time.time() + 1200
while time.time() < deadline:
    time.sleep(15)
    ready = [e for e in expected if os.path.exists(os.path.join(out, e))]
    if len(ready) == len(expected):
        print("ALL OUTPUTS READY:", ready)
        with open(log_path, encoding="utf-8") as lf:
            for line in lf.readlines()[-12:]:
                print(line.rstrip())
        break
else:
    print(f"TIMEOUT - ready {len(ready)}/{len(expected)}")
    with open(log_path, encoding="utf-8") as lf:
        for line in lf.readlines()[-15:]:
            print(line.rstrip())
    try:
        proc.kill()
    except Exception:
        pass
