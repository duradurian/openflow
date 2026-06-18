param([switch]$Setup)

$ErrorActionPreference = "Stop"

Set-Location $PSScriptRoot

if (-not (Test-Path ".\.venv\Scripts\python.exe")) {
    py -3.11 -m venv .venv
    $Setup = $true
}

if ($Setup) {
    .\.venv\Scripts\python.exe -m pip install -r requirements.txt
    .\.venv\Scripts\python.exe -m pip install -r requirements-gpu-windows.txt
}

.\.venv\Scripts\python.exe scripts\run_server.py
