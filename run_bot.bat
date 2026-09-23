@echo off
cd /d "%~dp0"
title Sea Explorer Control Deck
where py >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    py -3 sea_explorer_ui.py
) else (
    python sea_explorer_ui.py
)
if errorlevel 1 pause
