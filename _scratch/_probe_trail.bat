@echo off
setlocal enabledelayedexpansion
set "ARGS=--src "D:\photos\" --ratio 16x9"
python -c "import sys; print(sys.argv)" %ARGS%
