@echo off
setlocal enabledelayedexpansion
echo --src t | findstr /i /c:"--src" >nul & echo rc=!errorlevel!
