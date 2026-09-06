@echo off
setlocal EnableDelayedExpansion

REM VRY source/developer install path. Frozen releases do not require Python.
REM The maintained build target is Python 3.14.x (GitHub Actions uses 3.14.5).
for /f "tokens=2 delims= " %%I in ('python --version 2^>nul') do set PYTHON_VERSION=%%I

if not defined PYTHON_VERSION (
    call :error "Python 3.14.x is not installed or is not available as 'python' on PATH."
    exit /b 1
)

for /f "tokens=1-3 delims=." %%a in ("!PYTHON_VERSION!") do (
    set MAJOR=%%a
    set MINOR=%%b
)

if not "!MAJOR!"=="3" (
    call :error "Unsupported Python !PYTHON_VERSION!. VRY source installs currently target Python 3.14.x."
    exit /b 1
)
if not "!MINOR!"=="14" (
    call :error "Unsupported Python !PYTHON_VERSION!. VRY source installs currently target Python 3.14.x."
    exit /b 1
)

python -m pip install --upgrade pip
if errorlevel 1 (
    call :error "Failed to update pip."
    exit /b 1
)

python -m pip install -r requirements.txt
if errorlevel 1 (
    call :error "There was an error installing requirements. Check the output above."
    exit /b 1
)

echo.
echo Requirements were successfully installed for Python !PYTHON_VERSION!.
echo START.bat is the source/developer launcher. Normal users should use the frozen release.
echo.
pause
exit /b 0

:error
echo.
echo %~1
echo.
pause
exit /b 0
