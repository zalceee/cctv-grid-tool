@echo off
set "APP=%~dp0cgt.exe"

schtasks /create /tn "CCTVGridTool" /tr "\"%APP%\"" /sc onlogon /rl highest /f

if %errorlevel% equ 0 (
    echo CCTVGridTool task created successfully.
) else (
    echo Failed to create CCTVGridTool task.
)

pause
