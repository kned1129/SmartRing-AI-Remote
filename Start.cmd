@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (echo Run tools/install.ps1 first. & pause & exit /b 1)
".venv\Scripts\python.exe" "tools\launch.py"
