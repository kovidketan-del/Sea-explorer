@echo off
cd /d "%~dp0"
where pyw >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    start "" /B pyw -3 sea_explorer_ui.py
) else (
    start "" /B pythonw sea_explorer_ui.py
)
exit /b 0
