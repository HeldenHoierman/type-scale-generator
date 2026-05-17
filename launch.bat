@echo off
title Type Scale Generator
cd /d "%~dp0"
start /min "" cmd /c "timeout /t 1 /nobreak >nul && start http://localhost:5000"
python app.py
