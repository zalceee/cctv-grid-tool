Set objShell = CreateObject("WScript.Shell")

objShell.Run """" & Replace(WScript.ScriptFullName, "start.vbs", "launcher.bat") & """", 0, False

Set objShell = Nothing