Set WshShell = CreateObject("WScript.Shell")
strPath = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
WshShell.CurrentDirectory = strPath
WshShell.Run """" & strPath & "\.venv\Scripts\pythonw.exe"" """ & strPath & "\tray_app.py""", 0, False
