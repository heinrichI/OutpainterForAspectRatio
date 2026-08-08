import os
import subprocess
import sys
import time

d = os.path.dirname(__file__)
out = os.path.join(d, "output")

script = os.path.join(d, "run_outpaint.py")
env_file = os.path.join(d, ".env.8189")
src = os.path.join(d, "testImages")
log_path = os.path.join(out, "run_plugin.log")

cmd = [
    sys.executable, script,
    "--env", env_file,
    "--src", src,
    "--out", out,
    "--ratio", "16x9",
    "--steps", "4",
    "--cfg", "1.0",
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
old_mtime = os.path.getmtime(out_file) if os.path.exists(out_file) else 0
deadline = time.time() + 600
while time.time() < deadline:
    time.sleep(10)
    if os.path.exists(out_file) and os.path.getmtime(out_file) > old_mtime:
        print(f"Output updated: {os.path.getsize(out_file)} bytes")
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
