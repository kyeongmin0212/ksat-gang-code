' VBS wrapper for runner_analysis.ps1.
' Runs PowerShell hidden so closing a console window cannot kill the analysis.
' Synchronous + WScript.Quit propagates the PowerShell exit code to Task Scheduler.
Set s = CreateObject("WScript.Shell")
rc = s.Run("powershell.exe -NoProfile -ExecutionPolicy Bypass -File ""C:\Users\sji48\ksat_gang\runner_analysis.ps1""", 0, True)
WScript.Quit(rc)
