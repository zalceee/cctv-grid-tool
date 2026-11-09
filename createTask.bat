
@echo off

set "APPDIR=%~dp0"
set "LAUNCHER=%~dp0run_cgt.bat"

echo Creating CCTV Grid Tool scheduled task...

schtasks /create ^
 /tn "ITTools\CCTVGridTool" ^
 /tr "cmd.exe /c ""%LAUNCHER%""" ^
 /sc onstart ^
 /rl highest ^
 /f

if %errorlevel% equ 0 (
    echo.
    echo CCTVGridTool startup task created successfully.
) else (
    echo.
    echo Failed to create CCTVGridTool startup task.
    pause
    exit /b 1
)

echo.
echo Configuring Task Scheduler settings...

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
 "$task = Get-ScheduledTask -TaskName 'CCTVGridTool' -TaskPath '\ITTools\';" ^
 "$task.Settings.AllowDemandStart = $true;" ^
 "$task.Settings.StartWhenAvailable = $true;" ^
 "$task.Settings.StopIfGoingOnBatteries = $false;" ^
 "$task.Settings.DisallowStartIfOnBatteries = $false;" ^
 "$task.Settings.ExecutionTimeLimit = 'PT0S';" ^
 "$task.Settings.Compatibility = 4;" ^
 "Set-ScheduledTask -TaskName 'CCTVGridTool' -TaskPath '\ITTools\' -Settings $task.Settings"

if %errorlevel% equ 0 (
    echo Task settings configured successfully.
) else (
    echo Failed to configure some task settings.
)

echo.
echo Creating daily 11:00 AM trigger...

schtasks /create ^
 /tn "ITTools\CCTVGridTool_11AM" ^
 /tr "cmd.exe /c ""%LAUNCHER%""" ^
 /sc daily ^
 /st 11:00 ^
 /rl highest ^
 /f

if %errorlevel% equ 0 (
    echo Daily 11:00 AM task created successfully.
) else (
    echo Failed to create daily 11:00 AM task.
)

echo.
echo ========================================
echo CCTVGridTool Task Configuration
echo ========================================
echo Startup trigger                  : YES
echo Daily execution                  : 11:00 AM
echo Allow task to run on demand      : YES
echo Run if scheduled start missed   : YES
echo Start only on AC power           : NO
echo Stop when switching to battery   : NO
echo Execution time limit             : NONE
echo Configure for                    : Windows 10
echo ========================================

pause
