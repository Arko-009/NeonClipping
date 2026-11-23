$WshShell = New-Object -ComObject WScript.Shell
$desktopPaths = @(
    [System.Environment]::GetFolderPath([System.Environment+SpecialFolder]::Desktop),
    "C:\Users\R7\Desktop",
    "C:\Users\R7\OneDrive\Desktop"
) | Select-Object -Unique

foreach ($dp in $desktopPaths) {
    if (Test-Path $dp) {
        $lnkPath = Join-Path $dp "NeonClipping.lnk"
        if (Test-Path $lnkPath) {
            Remove-Item $lnkPath -Force -ErrorAction SilentlyContinue
        }
        $shortcut = $WshShell.CreateShortcut($lnkPath)
        $shortcut.TargetPath = "A:\temp\neonclipping\launch.bat"
        $shortcut.WorkingDirectory = "A:\temp\neonclipping"
        $shortcut.IconLocation = "A:\temp\neonclipping\NeonClip_v2.ico,0"
        $shortcut.Description = "NeonClipping Studio 2.0 - Autonomous AI Video Studio"
        $shortcut.Save()
        Write-Host "Created shortcut pointing to NeonClip_v2.ico: $lnkPath"
    }
}

# Restart Windows Explorer to rebuild desktop icon cache
try {
    Stop-Process -Name explorer -Force -ErrorAction SilentlyContinue
    Start-Sleep -Milliseconds 800
    Start-Process explorer
    Write-Host "Windows Explorer restarted successfully - icon cache rebuilt!"
} catch {
    Write-Host "Explorer restart notice: $($_.Exception.Message)"
}
