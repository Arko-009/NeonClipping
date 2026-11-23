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
        $shortcut.IconLocation = "A:\temp\neonclipping\NeonClip.ico,0"
        $shortcut.Description = "NeonClipping Studio 2.0 - Autonomous AI Video Studio"
        $shortcut.Save()
        Write-Host "Recreated shortcut: $lnkPath"
    }
}

# Tell Windows Explorer to refresh its icon cache immediately
try {
    Add-Type -TypeDefinition @"
    using System;
    using System.Runtime.InteropServices;
    public class ShellNotifier {
        [DllImport("shell32.dll")]
        public static extern void SHChangeNotify(int eventId, int flags, IntPtr item1, IntPtr item2);
        public static void RefreshIcons() {
            SHChangeNotify(0x08000000, 0x0000, IntPtr.Zero, IntPtr.Zero);
        }
    }
"@
    [ShellNotifier]::RefreshIcons()
    Write-Host "Windows Shell icon cache refreshed successfully!"
} catch {
    Write-Host "Icon refresh: $($_.Exception.Message)"
}
