@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
cd /d "%~dp0"

REM ============================================================
REM  Batch outpaint: images from SRC_DIR to target aspect ratio
REM  Uses the krita-ai-diffusion backend (ComfyUI + FLUX.2 Klein 4B)
REM ============================================================

REM Read SERVER_DIR from .env (default = plugin server location)
set "SERVER_DIR="
if exist ".env" (
  for /f "usebackq eol=# tokens=1,* delims==" %%a in (".env") do (
    set "_k=%%a"
    if /i "!_k!"=="SERVER_DIR" set "SERVER_DIR=%%b"
  )
)
if not defined SERVER_DIR set "SERVER_DIR=j:\AI_Image_Enchance\ai_diffusion\server"
if defined SERVER_DIR set "SERVER_DIR=%SERVER_DIR:"=%"

REM Prefer the bundled server venv python; fall back to system python
set "PY=%SERVER_DIR%\venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

REM Arguments: if the first argument is a plain path (no leading '-'),
REM treat it as --src (covers drag-and-drop onto the .bat); pass the rest
REM through. Otherwise forward all args as-is.
set "ARGS=%*"
if not "%~1"=="" (
  set "_FIRST=%~1"
  if not "!_FIRST:~0,1!"=="-" (
    set "ARGS=--src "%~1" %2 %3 %4 %5 %6 %7 %8 %9"
  )
)

REM No --src given? Ask for the source directory at startup.
set "ASKED=0"
echo %ARGS% | findstr /i /c:"--src" >nul && set "ASKED=1"
if "%ASKED%"=="0" (
  set /p "USER_SRC=Path to image folder [Enter = SRC_DIR from .env]: "
  if not "!USER_SRC!"=="" (
    REM strip a trailing backslash so the quoted path stays clean
    if "!USER_SRC:~-1!"=="\" set "USER_SRC=!USER_SRC:~0,-1!"
    set "ARGS=--src "!USER_SRC!" %*"
  )
)


echo OutpainterForAspectRatio2
echo   Server dir : %SERVER_DIR%
echo   Python     : %PY%
echo   Args       : %ARGS%
echo.
echo Usage examples:
echo   process_images.bat                         (use SRC_DIR from .env)
echo   process_images.bat --src "D:\photos"      (process this folder)
echo   process_images.bat "D:\photos"            (same, drag-and-drop)
echo   process_images.bat --src "D:\p" --out "D:\out" --ratio 16x9
echo.

"%PY%" run_outpaint.py %ARGS%
set "CODE=%ERRORLEVEL%"

echo.
if "%CODE%"=="0" (
  echo Done. Results are in the output folder.
) else (
  echo Finished with errors ^(code %CODE%^). See log above.
)
pause
exit /b %CODE%
