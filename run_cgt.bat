
@echo off
setlocal

cd /d "%~dp0"

:: Create logs folder
if not exist "%~dp0logs" mkdir "%~dp0logs"

:: Generate datetime (24-hour format)
for /f %%a in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set "DATETIME=%%a"

set "LOG_FILE=%~dp0logs\%DATETIME%.txt"

echo ======================================== > "%LOG_FILE%"
echo CGT Application Log - %DATETIME% >> "%LOG_FILE%"
echo ======================================== >> "%LOG_FILE%"

echo Running updater... >> "%LOG_FILE%" 2>&1
"%~dp0update.exe" >> "%LOG_FILE%" 2>&1

echo. >> "%LOG_FILE%"
echo Starting CCTV Grid Tool... >> "%LOG_FILE%"
"%~dp0cgt.exe" >> "%LOG_FILE%" 2>&1

echo. >> "%LOG_FILE%"
echo Process completed. >> "%LOG_FILE%"

endlocal
exit /b
