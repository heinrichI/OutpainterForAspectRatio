@echo off
setlocal enabledelayedexpansion

set "SRC="D:\q""
echo SRC=[%SRC%]

rem idiom A: unquoted compare with escaped quote
if !SRC:~-1!==^" (echo A last-is-quote) else (echo A last-not-quote)

rem idiom B: quoted compare with escaped quote
if "!SRC:~-1!"=="^"" (echo B last-is-quote) else (echo B last-not-quote)

rem idiom C: caret quote on both sides
if ^"!SRC:~-1!^"==^"^" (echo C last-is-quote) else (echo C last-not-quote)

rem idiom D: use findstr-like substring match via if ... == "..."

exit /b 0
