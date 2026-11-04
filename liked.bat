@echo off
setlocal
cd /d "%~dp0"

set "PY_EXE="
if exist ".venv\Scripts\python.exe" set "PY_EXE=.venv\Scripts\python.exe"
if not defined PY_EXE (
  where python >nul 2>&1 && set "PY_EXE=python"
)
if not defined PY_EXE (
  where py >nul 2>&1 && set "PY_EXE=py -3"
)

if not defined PY_EXE (
  echo Python was not found.
  echo Install Python and ensure it is on PATH, or create .venv in this project.
  exit /b 1
)

%PY_EXE% scripts\liked_add\liked_add.py %*
