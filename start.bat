@echo off
title Lifeline Emergency Command Center
echo ========================================================
echo       LIFELINE EMERGENCY RESOURCE ALLOCATION
echo ========================================================
echo.

cd /d "%~dp0api"

echo [1/2] Checking Python dependencies...
python -m pip install -r "%~dp0requirements.txt" -q

echo.
echo [2/2] Starting Lifeline API and Command Dashboard...
echo URL: http://localhost:8000
echo.
start http://localhost:8000
python -m uvicorn main:app --reload --port 8000 --host 0.0.0.0
pause

