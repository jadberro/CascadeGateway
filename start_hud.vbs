Set WshShell = CreateObject("WScript.Shell")
strPath = WshShell.CurrentDirectory
WshShell.Run Chr(34) & strPath & "\.venv\Scripts\pythonw.exe" & Chr(34) & " -m cascadegateway.hud.overlay", 0, False
Set WshShell = Nothing
