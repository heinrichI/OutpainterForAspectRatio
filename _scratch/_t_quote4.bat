@echo off
setlocal enabledelayedexpansion

echo --- test for with amp inside quotes ---
for %%i in ("D:\a&b") do echo got: %%i
echo done1

echo --- test for /f with amp ---
for /f "tokens=* delims= " %%i in ("D:\a&b") do echo got: %%i
echo done2

echo --- dequote via substitution ---
set "SRC="D:\a b""
set "SRC=!SRC:"=!"
echo SRC now [%SRC%]
echo done3

echo --- dequote + amp ---
set "SRC="D:\a&b""
set "SRC=!SRC:"=!"
echo SRC now [%SRC%]
echo done4

exit /b 0
