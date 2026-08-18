@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
cd /d "%~dp0"

REM ============================================================
REM  Batch outpaint (inplace): images from a folder to the target
REM  aspect ratio. Results are saved into a subfolder INSIDE the
REM  source folder:  <source>\outpaint_<ratio>
REM ============================================================

REM ---- Read SERVER_DIR / PY from .env ----
set "SERVER_DIR="
if exist ".env" (
  for /f "usebackq eol=# tokens=1,* delims==" %%a in (".env") do (
    set "_k=%%a"
    if /i "!_k!"=="SERVER_DIR" set "SERVER_DIR=%%b"
  )
)
if not defined SERVER_DIR set "SERVER_DIR=j:\AI_Image_Enchance\ai_diffusion\server"
if defined SERVER_DIR set "SERVER_DIR=%SERVER_DIR:"=%"
set "PY=%SERVER_DIR%\venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

REM ---- Read ASPECT_RATIO / SRC_DIR from .env ----
set "ASPECT_RATIO=screen"
set "ENV_SRC_DIR="
if exist ".env" (
  for /f "usebackq eol=# tokens=1,* delims==" %%i in (".env") do (
    set "_k=%%i"
    if /i "!_k!"=="ASPECT_RATIO" set "ASPECT_RATIO=%%j"
    if /i "!_k!"=="SRC_DIR" set "ENV_SRC_DIR=%%j"
  )
)

REM ---- Extract --src, --ratio, ratio_num and cleaned args via Python ----
REM Python writes 4 lines (src, ratio_override, ratio_num, cleaned_args).
REM Read them with set /p so empty leading fields are preserved correctly.
set "SRC_DIR="
set "RATIO_OVERRIDE="
set "RATIO_NUM="
set "CLEAN_ARGS="
set "TMPF=%TEMP%\inplace_src.txt"
del "%TMPF%" 2>nul
"%PY%" _extract_src.py "%TMPF%" "!ASPECT_RATIO!" %*
if exist "%TMPF%" (
  < "%TMPF%" (
    set /p "SRC_DIR="
    set /p "RATIO_OVERRIDE="
    set /p "RATIO_NUM="
    set /p "CLEAN_ARGS="
  )
)
del "%TMPF%" 2>nul

REM ---- Apply --ratio override if given ----
if defined RATIO_OVERRIDE set "ASPECT_RATIO=!RATIO_OVERRIDE!"
if not defined RATIO_NUM set "RATIO_NUM=1.78"

REM ---- Prompt the user if no source directory yet ----
if not defined SRC_DIR (
  set /p "USER_SRC=Path to image folder [Enter = SRC_DIR from .env]: "
  if "!USER_SRC!"=="" set "USER_SRC=!ENV_SRC_DIR!"
  if "!USER_SRC!"=="" (
    echo ERROR: no source directory given.
    pause
    exit /b 2
  )
  set "SRC_DIR=!USER_SRC!"
)
if "!SRC_DIR:~-1!"=="\" set "SRC_DIR=!SRC_DIR:~0,-1!"

REM ---- Build final args ----
set "OUT_DIR=%SRC_DIR%\outpaint_%RATIO_NUM%"
set "ARGS=--src "%SRC_DIR%" --out "%OUT_DIR%""
if defined CLEAN_ARGS set "ARGS=!ARGS! !CLEAN_ARGS!"

echo OutpainterForAspectRatio2 ^(inplace^)
echo   Source : %SRC_DIR%
echo   Output : %OUT_DIR%
echo   Ratio  : %ASPECT_RATIO%  ^(= %RATIO_NUM%^)
echo   Args   : %ARGS%
echo.

"%PY%" run_outpaint.py %ARGS%
set "CODE=%ERRORLEVEL%"

echo.
if "%CODE%"=="0" (echo Done. Results are in: %OUT_DIR%) else (echo Finished with errors ^(code %CODE%^). See log above.)
pause
exit /b %CODE%