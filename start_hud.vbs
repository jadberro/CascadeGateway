Set WshShell = CreateObject("WScript.Shell")
strPath = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
WshShell.CurrentDirectory = strPath
Set oEnv = WshShell.Environment("PROCESS")
oEnv("PYTHONPATH") = strPath & "\src;" & oEnv("PYTHONPATH")
WshShell.Run """" & strPath & "\.venv\Scripts\pythonw.exe"" -m cascadegateway.hud.overlay", 0, False
Set WshShell = Nothing
