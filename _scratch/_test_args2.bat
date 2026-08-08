@echo off
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
