@echo off
setlocal enabledelayedexpansion

set "_Q=""
echo Q=[%_Q%]

set "SRC="D:\q""
if "!SRC:~-1!"=="!_Q!" (echo A last-is-quote) else (echo A last-not-quote)
if "!SRC:~0,1!"=="!_Q!" (echo B first-is-quote) else (echo B first-not-quote)

set "SRC=D:\q"
if "!SRC:~-1!"=="!_Q!" (echo C last-is-quote) else (echo C last-not-quote)
if "!SRC:~0,1!"=="!_Q!" (echo D first-is-quote) else (echo D first-not-quote)

exit /b 0
