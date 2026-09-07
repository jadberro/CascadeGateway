# PowerShell script to create Desktop and Windows Startup shortcuts for CascadeGateway

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if (Test-Path (Join-Path $scriptDir "..\config.yaml")) {
    $baseDir = (Get-Item (Join-Path $scriptDir "..")).FullName
} else {
    $baseDir = $scriptDir
}

$pythonw = Join-Path $baseDir ".venv\Scripts\pythonw.exe"
$trayApp = Join-Path $baseDir "tray_app.py"
$iconPath = Join-Path $baseDir "assets\icon.ico"

$desktopPath = [System.Environment]::GetFolderPath([System.Environment+SpecialFolder]::Desktop)
$startupPath = [System.Environment]::GetFolderPath([System.Environment+SpecialFolder]::Startup)

$WshShell = New-Object -ComObject WScript.Shell

# 1. Desktop Shortcut
$desktopShortcutPath = Join-Path $desktopPath "RTX 5090 Model Cascade.lnk"
$desktopShortcut = $WshShell.CreateShortcut($desktopShortcutPath)
$desktopShortcut.TargetPath = $pythonw
$desktopShortcut.Arguments = "`"$trayApp`""
$desktopShortcut.WorkingDirectory = $baseDir
if (Test-Path $iconPath) {
    $desktopShortcut.IconLocation = "$iconPath,0"
}
$desktopShortcut.Description = "CascadeGateway Tray App (Local + Gemini)"
$desktopShortcut.Save()
Write-Host "Created Desktop Shortcut: $desktopShortcutPath"

# 2. Windows Startup Shortcut (Launches at Boot)
$startupShortcutPath = Join-Path $startupPath "RTX5090Cascade.lnk"
$startupShortcut = $WshShell.CreateShortcut($startupShortcutPath)
$startupShortcut.TargetPath = $pythonw
$startupShortcut.Arguments = "`"$trayApp`""
$startupShortcut.WorkingDirectory = $baseDir
if (Test-Path $iconPath) {
    $startupShortcut.IconLocation = "$iconPath,0"
}
$startupShortcut.Description = "CascadeGateway Auto-Boot Tray Service"
$startupShortcut.Save()
Write-Host "Created Windows Startup Shortcut: $startupShortcutPath"
