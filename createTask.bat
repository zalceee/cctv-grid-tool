@echo off

set "APPDIR=%~dp0"
set "LAUNCHER=%~dp0run_cgt.bat"

echo Creating CCTV Grid Tool scheduled task...

schtasks /create ^
 /tn "ITTools\CCTVGridTool" ^
 /tr "cmd.exe /c ""%LAUNCHER%""" ^
 /sc onlogon ^
 /rl highest ^
 /f

if %errorlevel% equ 0 (
    echo.
    echo CCTVGridTool task created successfully.
) else (
    echo.
    echo Failed to create CCTVGridTool task.
    pause
    exit /b 1
)

echo.
echo Configuring Task Scheduler settings...

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
 "$task = Get-ScheduledTask -TaskName 'CCTVGridTool' -TaskPath '\ITTool\';" ^
 "$task.Settings.AllowDemandStart = $true;" ^
 "$task.Settings.StartWhenAvailable = $true;" ^
 "$task.Settings.StopIfGoingOnBatteries = $false;" ^
 "$task.Settings.DisallowStartIfOnBatteries = $false;" ^
 "$task.Settings.ExecutionTimeLimit = 'PT0S';" ^
 "Set-ScheduledTask -TaskName 'CCTVGridTool' -TaskPath '\ITTool\' -Settings $task.Settings"

if %errorlevel% equ 0 (
    echo Task settings configured successfully.
) else (
    echo Failed to configure some task settings.
)

echo.
echo ========================================
echo CCTVGridTool Task Configuration
echo ========================================
echo Allow task to run on demand       : YES
echo Run task if scheduled start missed: YES
echo Start only on AC power            : NO
echo Stop when switching to battery    : YES
echo Execution time limit              : NONE
echo Force stop after time limit        : NO
echo ========================================

pause