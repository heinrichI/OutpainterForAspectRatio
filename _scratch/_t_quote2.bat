@echo off
setlocal enabledelayedexpansion

set "SRC="D:\q""
if !SRC:~0,1!==^" (echo A first-is-quote) else (echo A first-not-quote)

set "SRC=D:\q"
if !SRC:~0,1!==^" (echo B first-is-quote) else (echo B first-not-quote)

rem full normalize with idiom A
set "SRC="D:\q""
for /f "tokens=* delims= " %%i in ("!SRC!") do set "SRC=%%i"
:trim
if "!SRC:~-1!"==" "   set "SRC=!SRC:~0,-1!" & goto trim
if "!SRC:~-1!"=="\"   set "SRC=!SRC:~0,-1!" & goto trim
if !SRC:~-1!==^"      set "SRC=!SRC:~0,-1!" & goto trim
if !SRC:~0,1!==^"     set "SRC=!SRC:~1!"    & goto trim
echo C [%SRC%]

exit /b 0
