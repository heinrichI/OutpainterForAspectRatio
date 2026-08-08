@echo off
setlocal enabledelayedexpansion
echo === bare ===
echo --src F:	est --ratio 16x9 | findstr /i "--src" >nul & echo rc=!errorlevel!
echo === /c quoted ===
echo --src F:	est --ratio 16x9 | findstr /i /c:"--src" >nul & echo rc=!errorlevel!
echo === /c unquoted ===
echo --src F:	est --ratio 16x9 | findstr /i /c:--src >nul & echo rc=!errorlevel!
echo === bare src ===
echo --src F:	est --ratio 16x9 | findstr /i src >nul & echo rc=!errorlevel!
echo === two dashes only ===
echo --src F:	est --ratio 16x9 | findstr /i -- >nul & echo rc=!errorlevel!
echo === hello /c quoted ===
echo hello world | findstr /c:"hello" >nul & echo rc=!errorlevel!
echo === hello bare ===
echo hello world | findstr hello >nul & echo rc=!errorlevel!
echo === set ARGS then echo ===
set "ARGS=--src "D:\photos with spaces" --ratio 16x9"
echo !ARGS! | findstr /i "--src" >nul & echo rc=!errorlevel!
echo === ARGS echo delayed ===
echo !ARGS!
echo === ARGS echo percent ===
echo %ARGS%
