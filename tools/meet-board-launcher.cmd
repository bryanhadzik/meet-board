@echo off
rem Double-click to open the meet-board launcher (start/stop server, update from GitHub).
start "" powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0meet-board-launcher.ps1"
