@echo off
cd /d "%~dp0"
call venv\Scripts\python.exe mosaic_app.py %*
