@echo off
cd /d "%~dp0"
title Sea Explorer Bot
python sea_explorer_bot.py
echo.
echo Bot exited with code %ERRORLEVEL%.
pause
