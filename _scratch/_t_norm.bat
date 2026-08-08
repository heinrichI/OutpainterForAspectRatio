@echo off
setlocal enabledelayedexpansion

set "SRC=D:\p\"
call :n
echo 1 [%SRC%]

set "SRC= D:\a b "
call :n
echo 2 [%SRC%]

set "SRC="D:\q""
call :n
echo 3 [%SRC%]

set "SRC= D:\x\ "
call :n
echo 4 [%SRC%]

set "SRC="D:\z \""
call :n
echo 5 [%SRC%]

set "SRC=""
call :n
echo 6 [%SRC%]

set "SRC=D:\only"
call :n
echo 7 [%SRC%]

exit /b 0

:n
for /f "tokens=* delims= " %%i in ("!SRC!") do set "SRC=%%i"
:trim
if "!SRC:~-1!"==" "   set "SRC=!SRC:~0,-1!" & goto trim
if "!SRC:~-1!"=="\"   set "SRC=!SRC:~0,-1!" & goto trim
if "!SRC:~-1!"==""""  set "SRC=!SRC:~0,-1!" & goto trim
if "!SRC:~0,1!"=="""" set "SRC=!SRC:~1!"    & goto trim
exit /b 0
