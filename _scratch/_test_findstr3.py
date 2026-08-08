import subprocess

tests = [
    r'echo hello world | findstr /c:"hello" >nul && echo FOUND || echo NOTFOUND',
    r'echo --src test | findstr /c:"src" >nul && echo FOUND || echo NOTFOUND',
    r'echo --src test | findstr "src" >nul && echo FOUND || echo NOTFOUND',
    r'echo --src test | findstr "--" >nul && echo FOUND || echo NOTFOUND',
]
for t in tests:
    r = subprocess.run(["cmd", "/c", t], capture_output=True, text=True, timeout=20, errors="replace")
    print(f"CMD: {t}\n  -> {r.stdout.strip() or r.stderr.strip()}")
