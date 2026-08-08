@echo off
setlocal enabledelayedexpansion

set "SRC="D:\q""
for %%i in ("!SRC!") do set "SRC=%%~i"
echo 1 [%SRC%]

set "SRC=D:\p\"
for %%i in ("!SRC!") do set "SRC=%%~i"
echo 2 [%SRC%]

set "SRC="D:\z \""
for %%i in ("!SRC!") do set "SRC=%%~i"
echo 3 [%SRC%]

set "SRC=""
for %%i in ("!SRC!") do set "SRC=%%~i"
echo 4 [%SRC%]

set "SRC="D:\a b""
for %%i in ("!SRC!") do set "SRC=%%~i"
echo 5 [%SRC%]

set "SRC= "D:\q" "
for %%i in ("!SRC!") do set "SRC=%%~i"
echo 6 [%SRC%]

set "SRC=D:\a&b"
for %%i in ("!SRC!") do set "SRC=%%~i"
echo 7 [%SRC%]

exit /b 0
