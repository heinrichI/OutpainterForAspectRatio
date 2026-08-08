import os
import subprocess
import sys
import time

d = os.path.dirname(__file__)
out = os.path.join(d, "output")

script = os.path.join(d, "run_outpaint.py")
env_file = os.path.join(d, ".env.8189")
src = os.path.join(d, "testImages")
log_path = os.path.join(out, "run20.log")

cmd = [
    sys.executable, script,
    "--env", env_file,
    "--src", src,
    "--out", out,
    "--ratio", "16x9",
    "--steps", "20",
    "--cfg", "5.0",
    "--seed", "42",
    "--limit", "1",
    "--no-resume",
]

with open(log_path, "w") as log:
    proc = subprocess.Popen(
        cmd, stdout=log, stderr=subprocess.STDOUT,
        creationflags=0x00000008,
    )
    pid = proc.pid

print("BG PID:", pid)

out_file = os.path.join(out, "490005518_18504575899014999_5005429624106475873_n__2400x1350.png")
deadline = time.time() + 900
while time.time() < deadline:
    time.sleep(15)
    if os.path.exists(out_file) and os.path.getsize(out_file) > 100000:
        size = os.path.getsize(out_file)
        print(f"Output ready: {size} bytes")
        with open(log_path, encoding="utf-8") as lf:
            for line in lf.readlines()[-6:]:
                print(line.rstrip())
        break
else:
    print("TIMEOUT")
    try:
        with open(log_path, encoding="utf-8") as lf:
            for line in lf.readlines()[-8:]:
                print(line.rstrip())
    except Exception:
        pass
