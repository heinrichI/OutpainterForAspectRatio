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

echo OutpainterForAspectRatio2
echo   Server dir : %SERVER_DIR%
echo   Python     : %PY%
echo.

"%PY%" run_outpaint.py %*
set "CODE=%ERRORLEVEL%"

echo.
if "%CODE%"=="0" (
  echo Done. Results are in the output folder.
) else (
  echo Finished with errors ^(code %CODE%^). See log above.
)
pause
exit /b %CODE%
