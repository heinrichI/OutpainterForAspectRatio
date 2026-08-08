@echo off
setlocal enabledelayedexpansion
REM test ARGS parsing logic
set "ARGS=%*"
if not "%~1"=="" (
  set "_FIRST=%~1"
  if not "!_FIRST:~0,1!"=="-" (
    if exist "%~1\" (
      set "ARGS=--src "%~1" %2 %3 %4 %5 %6 %7 %8 %9"
    )
  )
)
echo ARGS=[%ARGS%]
endlocal
