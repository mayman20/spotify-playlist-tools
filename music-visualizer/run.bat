@echo off
cd /d "%~dp0"
echo Usage: run.bat [right^|top^|bottom]
echo   right   = vertical right edge (default)
echo   top     = horizontal top bar
echo   bottom  = horizontal bottom bar
echo.

if "%1"=="top"    python visualizer.py --top    & goto end
if "%1"=="bottom" python visualizer.py --bottom & goto end
python visualizer.py

:end
