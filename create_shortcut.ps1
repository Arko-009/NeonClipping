$WshShell = New-Object -ComObject WScript.Shell
$desktopPaths = @(
    [System.Environment]::GetFolderPath([System.Environment+SpecialFolder]::Desktop),
    "C:\Users\R7\Desktop",
    "C:\Users\R7\OneDrive\Desktop"
) | Select-Object -Unique

foreach ($dp in $desktopPaths) {
    if (Test-Path $dp) {
        $lnkPath = Join-Path $dp "NeonClipping.lnk"
        $shortcut = $WshShell.CreateShortcut($lnkPath)
        $shortcut.TargetPath = "A:\temp\neonclipping\launch.bat"
        $shortcut.WorkingDirectory = "A:\temp\neonclipping"
        $shortcut.IconLocation = "A:\temp\neonclipping\NeonClip.ico,0"
        $shortcut.Description = "NeonClipping Studio 2.0 - Autonomous AI Video Studio"
        $shortcut.Save()
        Write-Host "Created shortcut: $lnkPath"
    }
}
