$project = Split-Path -Parent $MyInvocation.MyCommand.Path
$script = Join-Path $project 'sea_explorer_ui.py'
$icon = Join-Path $project 'sea_explorer.ico'
$desktop = [Environment]::GetFolderPath('Desktop')
$launcher = Get-Command pyw.exe -ErrorAction Stop

if (-not (Test-Path -LiteralPath $script -PathType Leaf)) {
    throw "Dashboard script not found: $script"
}
if (-not (Test-Path -LiteralPath $icon -PathType Leaf)) {
    throw "Desktop icon not found: $icon"
}

$shortcutPath = Join-Path $desktop 'Sea Explorer Bot.lnk'
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $launcher.Source
$shortcut.Arguments = '-3 "' + $script + '" --autostart'
$shortcut.WorkingDirectory = $project
$shortcut.IconLocation = $icon + ',0'
$shortcut.Description = 'Reconnect the selected phone and start the Sea Explorer bot'
$shortcut.Save()
Write-Output $shortcutPath
