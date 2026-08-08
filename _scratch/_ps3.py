import subprocess

o = subprocess.run(
    ["wmic", "process", "where", "name='python.exe'", "get", "ProcessId,CommandLine"],
    capture_output=True, text=True, errors="replace", timeout=15)
for l in o.stdout.splitlines():
    if "run_outpaint" in l:
        print(l.strip()[:250])
