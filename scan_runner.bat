@echo off
REM PKScreener automated scan runner for Windows CMD
REM This script bypasses TTY detection issues

setlocal enabledelayedexpansion

cd /d "D:\INVESTMENT\AI Automation\Indian Stock Screener"

REM Set environment variables to help TTY detection
set PYTHONUNBUFFERED=1
set TERM=xterm

REM Activate virtual environment
call .venv\Scripts\activate.bat

REM Verify Python is working
python --version

REM Run PKScreener with environment variables set in PowerShell and dump to file
REM This uses the scanner option 12, index 10 (typically a volatility scanner)
REM -a Y = answer default yes to all prompts
REM -e = exit after single run
REM -o 12:10 = menu option 12, scanner option 10

echo Running PKScreener stock scan...
echo.

REM Try running with the command that worked in PowerShell
powershell -NoProfile -Command "$env:PYTHONUNBUFFERED='1'; $env:TERM='xterm'; & '.\.venv\Scripts\Activate.ps1'; pkscreener -a Y -o 12:10 -e" 2>&1

echo.
echo Scan completed. Checking results in results\Data\ folder...
echo.

dir results\Data\

pause
