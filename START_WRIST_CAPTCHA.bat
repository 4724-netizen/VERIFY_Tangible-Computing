@echo off
title Wrist CAPTCHA Launcher
color 0A

:: Kill any existing Python
taskkill /f /im python.exe >nul 2>&1

echo ======================================
echo    WRIST CAPTCHA - Motion Analysis
echo ======================================
echo.
echo Starting backend...
echo ======================================
echo.

:: Run Python in the SAME window
python captcha.py