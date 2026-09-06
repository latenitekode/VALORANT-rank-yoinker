@echo off
setlocal
set "SOURCE=%~1"
set "DEST=%~2"

if not exist "%SOURCE%" exit /b 2
if not exist "%DEST%" exit /b 3

robocopy "%SOURCE%" "%DEST%" /E /MOVE >NUL
set "ROBOCOPY_CODE=%ERRORLEVEL%"
if %ROBOCOPY_CODE% GEQ 8 exit /b %ROBOCOPY_CODE%

cd /d "%DEST%"
start "" "vry.exe"
exit /b 0
