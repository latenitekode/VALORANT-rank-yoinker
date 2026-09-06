@echo off
cd /d "%~dp0"
echo [VRY source/developer launcher]
echo Normal end users should run vry.exe from the frozen Windows release.
echo.
python "main.py"
pause
