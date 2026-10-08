param(
    [ValidateRange(1, 65535)][int]$Port = 8768,
    [switch]$Restart,
    [switch]$LocalOnly,
    [string]$DataDir = ''
)
$ErrorActionPreference = 'Stop'
$demoRoot = $PSScriptRoot
$taskPython = Join-Path $demoRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) { throw 'Run .\setup.ps1 first to prepare the project Python environment.' }
if ($Restart) { & (Join-Path $demoRoot 'stop.ps1') -Port $Port }
$lanEnabled = -not $LocalOnly
function Show-ConnectionDetails {
    param([string]$BaseUrl)
    Write-Output "Operator: $BaseUrl/operator.html"
    try {
        $connection = Invoke-RestMethod -Uri "$BaseUrl/api/connection" -TimeoutSec 3
        if ($connection.lan_enabled) {
            Write-Output 'iPad: join the same LAN, then scan the QR code in the operator connection panel or open:'
            foreach ($url in $connection.participant_urls) { Write-Output "  $url" }
            if (-not $connection.participant_urls) { Write-Output 'No LAN address detected. Connect Wi-Fi/Ethernet and refresh the operator panel.' }
        } else { Write-Output ('Local-only mode: ' + $connection.local_participant_url) }
    } catch { Write-Warning 'Could not load connection details. Check the operator panel.' }
}
$previewUrl = "http://127.0.0.1:$Port"
$health = $null
try { $health = Invoke-RestMethod -Uri "$previewUrl/api/health" -TimeoutSec 2 } catch {}
if ($health -and $health.service -eq 'chengsi-backend' -and $health.api_version -eq '1') {
    if ([bool]$health.lan_enabled -ne $lanEnabled) {
        throw 'A server is already running in a different LAN/local-only mode. Stop it, then restart with the desired mode (or use -Restart for this managed server).'
    }
    Write-Output "Already running: $previewUrl/"
    Show-ConnectionDetails -BaseUrl $previewUrl
    return
}
$listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($listener) { throw "Port $Port is occupied. Use -Restart only for this project's managed server, or select another port." }
$managedPidFile = Join-Path $demoRoot 'preview-server.pid'
if (Test-Path -LiteralPath $managedPidFile) {
    $savedProcessId = (Get-Content -LiteralPath $managedPidFile -Raw).Trim()
    if ($savedProcessId -match '^\d+$' -and (Get-Process -Id ([int]$savedProcessId) -ErrorAction SilentlyContinue)) {
        throw 'A managed PID is still running. Stop the existing server using its original -Port before starting another.'
    }
}
$serverScript = Join-Path $demoRoot 'server.py'
$serverArgs = '-X utf8 -u "' + $serverScript + '" --port ' + $Port
if ($lanEnabled) { $serverArgs += ' --lan' }
if ($DataDir) {
    if ($DataDir.Contains('"')) { throw 'DataDir cannot contain a double quote.' }
    $absoluteDataDir = [System.IO.Path]::GetFullPath($DataDir)
    if ($absoluteDataDir.EndsWith('\')) { $absoluteDataDir += '.' }
    $serverArgs += ' --data-dir "' + $absoluteDataDir + '"'
}
$serverProcess = Start-Process -FilePath $taskPython -ArgumentList $serverArgs -WorkingDirectory $demoRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $demoRoot 'backend-server.log') -RedirectStandardError (Join-Path $demoRoot 'backend-server-error.log')
Set-Content -LiteralPath (Join-Path $demoRoot 'preview-server.pid') -Value $serverProcess.Id
$ready = $false
for ($attempt = 0; $attempt -lt 60; $attempt++) {
    Start-Sleep -Milliseconds 250
    try {
        $health = Invoke-RestMethod -Uri "$previewUrl/api/health" -TimeoutSec 1
        if ($health.service -eq 'chengsi-backend' -and $health.api_version -eq '1' -and [bool]$health.lan_enabled -eq $lanEnabled) { $ready = $true; break }
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
Show-ConnectionDetails -BaseUrl $previewUrl
Write-Output "API schema: $previewUrl/openapi.json"
