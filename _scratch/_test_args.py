import subprocess

cases = [
    [r"F:\E\SourcePython3\OutpainterForAspectRatio2\testImages", "--dry-run", "--no-resume"],
    ["--src", r"F:\E\SourcePython3\OutpainterForAspectRatio2\testImages", "--ratio", "16x9"],
    [],
    ["D:\\photos with spaces"],
]
for c in cases:
    r = subprocess.run(
        [r"f:\E\SourcePython3\OutpainterForAspectRatio2\_test_args.bat", *c],
        capture_output=True, text=True, timeout=30)
    out = r.stdout.strip()
    for line in out.splitlines():
        if line.startswith("ARGS="):
            print(f"in={c!r}\n  -> {line}")
