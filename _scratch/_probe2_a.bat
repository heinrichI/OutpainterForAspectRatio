@echo off
setlocal enabledelayedexpansion
echo --src t | findstr /i "--src" >nul & echo rc=!errorlevel!
