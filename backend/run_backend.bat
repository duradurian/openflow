@echo off
setlocal
cd /d "%~dp0"
set SETUP=0

if not exist ".venv\Scripts\python.exe" (
  py -3.11 -m venv .venv
  set SETUP=1
)

if "%1"=="--setup" set SETUP=1

if "%SETUP%"=="1" (
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  ".venv\Scripts\python.exe" -m pip install -r requirements-gpu-windows.txt
)

".venv\Scripts\python.exe" scripts\run_server.py
