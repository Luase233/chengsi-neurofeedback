param([int]$Port = 8768, [switch]$Restart)
$ErrorActionPreference = 'Stop'
$demoRoot = $PSScriptRoot
$taskPython = Join-Path $demoRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) { throw 'Run .\setup.ps1 first to prepare the project Python environment.' }
if ($Restart) { & (Join-Path $demoRoot 'stop.ps1') -Port $Port }
$previewUrl = "http://127.0.0.1:$Port"
$health = $null
try { $health = Invoke-RestMethod -Uri "$previewUrl/api/health" -TimeoutSec 2 } catch {}
if ($health -and $health.service -eq 'chengsi-backend' -and $health.api_version -eq '1') {
    Write-Output "Already running: $previewUrl/"
    return
}
$listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($listener) { throw "Port $Port is occupied. Use -Restart only for this project's managed server, or select another port." }
$serverScript = Join-Path $demoRoot 'server.py'
$serverArgs = '-X utf8 -u "' + $serverScript + '" --port ' + $Port
$serverProcess = Start-Process -FilePath $taskPython -ArgumentList $serverArgs -WorkingDirectory $demoRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $demoRoot 'backend-server.log') -RedirectStandardError (Join-Path $demoRoot 'backend-server-error.log')
Set-Content -LiteralPath (Join-Path $demoRoot 'preview-server.pid') -Value $serverProcess.Id
$ready = $false
for ($attempt = 0; $attempt -lt 60; $attempt++) {
    Start-Sleep -Milliseconds 250
    try {
        $health = Invoke-RestMethod -Uri "$previewUrl/api/health" -TimeoutSec 1
        if ($health.service -eq 'chengsi-backend' -and $health.api_version -eq '1') { $ready = $true; break }
    } catch {}
    $serverProcess.Refresh()
    if ($serverProcess.HasExited) { break }
}
if (-not $ready) { throw 'Backend did not start. Check backend-server-error.log.' }
$readyListener = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop | Select-Object -ExpandProperty OwningProcess -Unique)
if ($readyListener.Count -ne 1) { throw 'Could not identify the backend listener.' }
$listenerProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $($readyListener[0])"
if ($listenerProcess.CommandLine -notmatch [regex]::Escape($serverScript) -or ($listenerProcess.ProcessId -ne $serverProcess.Id -and $listenerProcess.ParentProcessId -ne $serverProcess.Id)) {
    throw 'The backend listener does not belong to the process just launched.'
}
# Windows virtual environments can launch a base-Python child which owns the socket.
Set-Content -LiteralPath (Join-Path $demoRoot 'preview-server.pid') -Value $listenerProcess.ProcessId
Write-Output "Open $previewUrl/"
Write-Output "API schema: $previewUrl/openapi.json"
