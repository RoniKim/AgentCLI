@echo off
setlocal EnableExtensions

set "ROOT=%~dp0"
cd /d "%ROOT%"

set "HOST=127.0.0.1"
set "PORT=8000"

if not "%AGENTCLI_WEB_HOST%"=="" set "HOST=%AGENTCLI_WEB_HOST%"
if not "%AGENTCLI_WEB_PORT%"=="" set "PORT=%AGENTCLI_WEB_PORT%"
if not "%~1"=="" set "PORT=%~1"

set "FREE_PORT="
call :FindFreePort "%HOST%" "%PORT%"
if not defined FREE_PORT (
    echo [ERROR] Could not find a free port from %PORT% to %PORT%+99.
    pause
    exit /b 1
)
if not "%FREE_PORT%"=="%PORT%" (
    echo [WARN] Port %PORT% is already in use. Using %FREE_PORT% instead.
)
set "PORT=%FREE_PORT%"

if exist "%ROOT%.venv\Scripts\activate.bat" (
    call "%ROOT%.venv\Scripts\activate.bat"
) else (
    echo [WARN] .venv was not found. Using the current Python environment.
)

echo [INFO] Starting AgentCLI Web Console
echo [INFO] Repo: %ROOT%
echo [INFO] URL:  http://%HOST%:%PORT%/
echo.

set "AGENTCLI_WEB_RESOLVED_REPO=%ROOT%"
set "AGENTCLI_WEB_RESOLVED_HOST=%HOST%"
set "AGENTCLI_WEB_RESOLVED_PORT=%PORT%"

start "" "http://%HOST%:%PORT%/"
python -c "import os; from agent_runner.web import serve; serve(repo=os.environ['AGENTCLI_WEB_RESOLVED_REPO'], host=os.environ['AGENTCLI_WEB_RESOLVED_HOST'], port=int(os.environ['AGENTCLI_WEB_RESOLVED_PORT']), enable_runner_controls=True)"
set "WEB_RC=%ERRORLEVEL%"

if not "%WEB_RC%"=="0" (
    echo.
    echo [ERROR] AgentCLI Web Console exited with an error.
    pause
)

endlocal & exit /b %WEB_RC%

:FindFreePort
set "CHECK_HOST=%~1"
set "CHECK_PORT=%~2"
for /f "usebackq delims=" %%P in (`powershell -NoProfile -ExecutionPolicy Bypass -Command "$hostName='%CHECK_HOST%'; $start=[int]'%CHECK_PORT%'; $ip=[Net.IPAddress]::Loopback; if ($hostName -eq '0.0.0.0') { $ip=[Net.IPAddress]::Any } elseif ($hostName -eq 'localhost') { $ip=[Net.IPAddress]::Loopback } else { try { $ip=[Net.IPAddress]::Parse($hostName) } catch { $ip=[Net.IPAddress]::Loopback } }; for ($p=$start; $p -lt ($start + 100); $p++) { $listener=$null; try { $listener=[Net.Sockets.TcpListener]::new($ip, $p); $listener.Start(); $listener.Stop(); Write-Output $p; exit 0 } catch { if ($listener) { $listener.Stop() } } }; exit 1"`) do (
    set "FREE_PORT=%%P"
)
if not defined FREE_PORT exit /b 1
exit /b 0
