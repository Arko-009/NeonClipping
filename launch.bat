@echo off
setlocal
title NeonClipping Studio 2.0
cd /d "%~dp0"

:: 1. Clear any stale proxy variables
set HTTP_PROXY=
set HTTPS_PROXY=
set ALL_PROXY=
set http_proxy=
set https_proxy=
set all_proxy=

echo =====================================================================
echo                NEONCLIPPING STUDIO 2.0 - LOCAL AI
echo =====================================================================
echo.

:: 2. Check if port 8770 is already running
powershell -NoProfile -ExecutionPolicy Bypass -Command "$c = (Get-NetTCPConnection -LocalPort 8770 -State Listen -ErrorAction SilentlyContinue); if ($c) { exit 1 } else { exit 0 }"
if errorlevel 1 (
    echo [*] NeonClipping Studio is ALREADY running on port 8770!
    echo [*] Opening Studio in your browser...
    start "" "http://127.0.0.1:8770"
    ping 127.0.0.1 -n 3 >nul
    exit /b 0
)

:: 3. Launch background health waiter to pop browser when server is ready
echo [*] Starting Studio background poller...
start "" powershell -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -Command "$ready = $false; for ($i=0; $i -lt 30; $i++) { try { $r = Invoke-WebRequest -Uri 'http://127.0.0.1:8770/sources' -UseBasicParsing -TimeoutSec 1 -ErrorAction Stop; if ($r.StatusCode -eq 200) { $ready = $true; break } } catch {}; Start-Sleep -Milliseconds 500 }; if ($ready) { Start-Process 'http://127.0.0.1:8770' }"

:: 4. Start Python app in this console window (foreground)
echo [*] Initializing AI Models and Studio Server...
echo [*] Studio URL : http://127.0.0.1:8770
echo [*] To STOP   : Close this window from the Taskbar or press Ctrl+C
echo.
echo =====================================================================
echo.

.\.venv\Scripts\python.exe app.py

echo.
echo [*] NeonClipping Studio has stopped.
