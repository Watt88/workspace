@echo off
rem reader3: starts the reader for this computer and other devices in the same WiFi network
cd /d "%~dp0"
chcp 65001 >nul

where uv >nul 2>nul
if errorlevel 1 (
  echo Installing uv...
  powershell -ExecutionPolicy ByPass -NoProfile -Command "irm https://astral.sh/uv/install.ps1 | iex"
  set "PATH=%USERPROFILE%\.local\bin;%PATH%"
)

set HOST=0.0.0.0
set PORT=8123
start "" http://localhost:8123
if exist .env (uv run --env-file .env server.py) else (uv run server.py)
pause
