
@echo off
setlocal

cd /d "%~dp0"

:: Create logs folder
if not exist "%~dp0logs" mkdir "%~dp0logs"

:: Generate datetime
for /f %%a in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set "DATETIME=%%a"

set "LOG_FILE=%~dp0logs\%DATETIME%.txt"
set "TEMP_LOG=%TEMP%\cgt_updater_%RANDOM%.txt"

:: Initialize log
echo ======================================== > "%LOG_FILE%"
echo CGT Application Log - %DATETIME% >> "%LOG_FILE%"
echo ======================================== >> "%LOG_FILE%"

:: Run updater
echo Running updater...
echo Running updater... >> "%LOG_FILE%"

"%~dp0update.exe" > "%TEMP_LOG%" 2>&1

:: Display and save updater output
type "%TEMP_LOG%"
type "%TEMP_LOG%" >> "%LOG_FILE%"

del "%TEMP_LOG%"

:: Run CGT
echo Starting CCTV Grid Tool...
echo Starting CCTV Grid Tool... >> "%LOG_FILE%"

"%~dp0cgt.exe" >> "%LOG_FILE%" 2>&1

echo Process completed.
echo Process completed. >> "%LOG_FILE%"

endlocal
exit /b
