@echo off
setlocal enabledelayedexpansion
set "ARGS=--src D:\p"
echo %ARGS% | findstr /i "--src" >nul & echo rc=!errorlevel!
