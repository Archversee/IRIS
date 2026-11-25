@echo off
rem Double-click to set up MAVIS's live lip-sync (MuseTalk) on this PC.
rem Runs setup_lipsync.ps1 next to it; see docs\LIPSYNC_SETUP.md.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup_lipsync.ps1" %*
echo.
pause
