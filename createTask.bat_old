@echo off

set "APPDIR=%~dp0"
set "LAUNCHER=%~dp0run_cgt.bat"

schtasks /create ^
 /tn "ITTool\CCTVGridTool" ^
 /tr "cmd.exe /c ""%LAUNCHER%""" ^
 /sc onlogon ^
 /rl highest ^
 /f

if %errorlevel% equ 0 (
    echo CCTVGridTool task created successfully.
) else (
    echo Failed to create CCTVGridTool task.
)

pause