@echo off
setlocal

cd /d "%~dp0"

:: Create logs folder
if not exist "%~dp0logs" mkdir "%~dp0logs"

:: Generate datetime
for /f %%a in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set "DATETIME=%%a"

set "LOG_FILE=%~dp0logs\%DATETIME%.txt"

echo ======================================== > "%LOG_FILE%"
echo CGT Application Log - %DATETIME% >> "%LOG_FILE%"
echo ======================================== >> "%LOG_FILE%"

:: Run updater - DISPLAY output and SAVE to log
echo Running updater...
echo Running updater... >> "%LOG_FILE%"
"%~dp0update.exe" 2>&1 | powershell -NoProfile -Command "$input | Tee-Object -FilePath '%LOG_FILE%' -Append"

:: Run CGT - HIDE output and SAVE to log
echo Starting CCTV Grid Tool...
echo Starting CCTV Grid Tool... >> "%LOG_FILE%"
"%~dp0cgt.exe" >> "%LOG_FILE%" 2>&1

echo Process completed.
echo Process completed. >> "%LOG_FILE%"

endlocal
exit /b