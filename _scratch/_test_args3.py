import subprocess

bat = r"f:\E\SourcePython3\OutpainterForAspectRatio2\_test_args2.bat"
bat_content = r'''@echo off
setlocal enabledelayedexpansion
set "ARGS=%*"
if not "%~1"=="" (
  set "_FIRST=%~1"
  if not "!_FIRST:~0,1!"=="-" (
    set "ARGS=--src "%~1" %2 %3 %4 %5 %6 %7 %8 %9"
  )
)
set "ASKED=0"
echo %ARGS% | findstr /i "--src" >nul && set "ASKED=1"
if "%ASKED%"=="0" (
  set /p "USER_SRC=Path to image folder [Enter = SRC_DIR from .env]: "
  if not "!USER_SRC!"=="" (
    set "ARGS=--src "!USER_SRC!" %*"
  )
)
echo RESULT=[%ARGS%]
endlocal
'''
with open(bat, "w", encoding="utf-8") as f:
    f.write(bat_content)

cases = [
    ([], "D:\\photos with spaces\n", "interactive with spaces"),
    ([], "\n", "interactive empty (use .env)"),
    (["--ratio", "16x9"], "E:\\another dir\n", "interactive + extra args"),
    ([r"F:\E\SourcePython3\OutpainterForAspectRatio2\testImages", "--dry-run"], None, "drag-drop path"),
    (["--src", r"F:\E\SourcePython3\OutpainterForAspectRatio2\testImages", "--ratio", "16x9"], None, "explicit --src"),
    ([], None, "no args, no input (EOF)"),
]
for args, stdin, label in cases:
    try:
        r = subprocess.run(
            [bat, *args], input=stdin, capture_output=True, text=True,
            timeout=30, errors="replace")
        out = "\n".join(l for l in r.stdout.splitlines() if l.startswith("RESULT=") or "Path to image" in l)
        print(f"{label:35s}: {out}")
    except Exception as e:
        print(f"{label:35s}: ERROR {e}")
