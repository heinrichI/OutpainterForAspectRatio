import subprocess

tests = [
    r'echo --src F:\test --ratio 16x9 | findstr /i /c:"--src" >nul && echo FOUND || echo NOTFOUND',
    r'set "ARGS=--src F:\test --ratio 16x9" & echo %ARGS% | findstr /i /c:"--src" >nul && echo FOUND || echo NOTFOUND',
    r'set "ARGS=--ratio 16x9" & echo %ARGS% | findstr /i /c:"--src" >nul && echo FOUND || echo NOTFOUND',
    r'echo --src "D:\photos with spaces" | findstr /i /c:"--src" >nul && echo FOUND || echo NOTFOUND',
]
for t in tests:
    r = subprocess.run(["cmd", "/c", t], capture_output=True, text=True, timeout=20, errors="replace")
    print(f"CMD: {t}\n  -> {r.stdout.strip() or r.stderr.strip()}")
