param(
    [ValidateRange(1, 65535)][int]$Port = 8768,
    [switch]$Restart,
    [switch]$LocalOnly,
    [string]$DataDir = '',
    [switch]$PassThru
)
$ErrorActionPreference = 'Stop'
$demoRoot = [IO.Path]::GetFullPath($PSScriptRoot)
$serverScript = Join-Path $demoRoot 'server.py'
$taskPython = Join-Path $demoRoot '.venv\Scripts\python.exe'
$managedPidFile = Join-Path $demoRoot 'preview-server.pid'
$managedStateFile = Join-Path $demoRoot 'runtime\preview-server.json'
. (Join-Path $demoRoot 'launcher-process.ps1')
if (-not (Test-Path -LiteralPath $taskPython)) { throw 'Run .\setup.ps1 first to prepare the project Python environment.' }
if ($Restart) { & (Join-Path $demoRoot 'stop.ps1') | Out-Host }
$lanEnabled = -not $LocalOnly
function Show-LauncherInfo {
    param([string]$Message)
    if ($PassThru) { Write-Host $Message } else { Write-Output $Message }
}
function Show-ConnectionDetails {
    param([string]$BaseUrl)
    Show-LauncherInfo "Operator: $BaseUrl/operator.html"
    try {
        $connection = Invoke-RestMethod -Uri "$BaseUrl/api/connection" -TimeoutSec 3
        if ($connection.lan_enabled) {
            Show-LauncherInfo 'iPad: join the same LAN, then scan the QR code in the operator connection panel or open:'
            foreach ($url in $connection.participant_urls) { Show-LauncherInfo "  $url" }
            if (-not $connection.participant_urls) { Show-LauncherInfo 'No LAN address detected. Connect Wi-Fi/Ethernet and refresh the operator panel.' }
        } else { Show-LauncherInfo ('Local-only mode: ' + $connection.local_participant_url) }
    } catch { Write-Warning 'Could not load connection details. Check the operator panel.' }
}
function Test-ManagedHealth {
    param($Server)
    if (-not (Get-ChengsiListener -ProcessId $Server.pid -ActualPort $Server.port)) { return $false }
    try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:$($Server.port)/api/health" -TimeoutSec 1
        return $health.service -eq 'chengsi-backend' -and $health.api_version -eq '1' -and [bool]$health.lan_enabled -eq $Server.lan_enabled
    } catch { return $false }
}
$launcherLock = Enter-ChengsiLauncherLock
try {
    $managed = Get-ChengsiManagedServer
    $alreadyRunning = [bool]$managed
    if ($managed) {
        if ($managed.lan_enabled -ne $lanEnabled) { throw 'This project is running in a different LAN/local-only mode. Use -Restart to change it.' }
        if ($DataDir -and [IO.Path]::GetFullPath($DataDir) -ne $managed.data_dir) { throw 'This project is running with a different DataDir. Use -Restart to change it.' }
        $ready = $false
        for ($attempt = 0; $attempt -lt 60; $attempt++) {
            if (Test-ManagedHealth $managed) { $ready = $true; break }
            if (-not (Get-ChengsiProcess -ProcessId $managed.pid)) { break }
            Start-Sleep -Milliseconds 250
        }
        if (-not $ready) { throw 'The managed server is not ready. Check backend-server-error.log or use -Restart.' }
        Save-ChengsiManagedServer $managed
        Show-LauncherInfo "Already running: http://127.0.0.1:$($managed.port)/"
    } else {
        $preferredPort = $Port
        $ready = $false
        for ($bindAttempt = 0; $bindAttempt -lt 10; $bindAttempt++) {
            $actualPort = Get-ChengsiAvailablePort -PreferredPort $preferredPort -LanEnabled $lanEnabled
            if ($actualPort -ne $Port) { Show-LauncherInfo "Port $Port is occupied; using available port $actualPort." }
            $serverArgs = @((Join-Path $demoRoot 'launcher_state.py'), 'spawn', '--root', $demoRoot, '--port', $actualPort)
            if ($lanEnabled) { $serverArgs += '--lan' }
            if ($DataDir) {
                if ($DataDir.Contains('"')) { throw 'DataDir cannot contain a double quote.' }
                $absoluteDataDir = [IO.Path]::GetFullPath($DataDir)
                if ($absoluteDataDir.EndsWith('\')) { $absoluteDataDir += '.' }
                $serverArgs += @('--data-dir', $absoluteDataDir)
            }
            $launchedId = & $taskPython @serverArgs
            if ($LASTEXITCODE -ne 0 -or $launchedId -notmatch '^\d+$') { throw 'Could not launch the background backend. Check the Python environment.' }
            $launchedProcessId = [int]$launchedId
            $serverExited = $false
            Set-Content -LiteralPath $managedPidFile -Value $launchedProcessId -Encoding ASCII
            if (Test-Path -LiteralPath $managedStateFile) { Remove-Item -LiteralPath $managedStateFile }
            for ($attempt = 0; $attempt -lt 60; $attempt++) {
                Start-Sleep -Milliseconds 250
                $managed = Get-ChengsiManagedServer
                if ($managed -and (Test-ManagedHealth $managed)) { $ready = $true; break }
                if (-not (Get-Process -Id $launchedProcessId -ErrorAction SilentlyContinue)) { $serverExited = $true; break }
            }
            if ($ready) { Save-ChengsiManagedServer $managed; break }
            # Retry only an actual bind race. Other startup errors retain their log.
            $startupError = Get-Content -LiteralPath (Join-Path $demoRoot 'backend-server-error.log') -Raw -ErrorAction SilentlyContinue
            $bindRace = $serverExited -and ($startupError -match 'address already in use|10048|Errno 98|Errno 48|error while attempting to bind')
            if (-not $bindRace) { throw 'Backend did not start. Check backend-server-error.log.' }
            Remove-Item -LiteralPath $managedPidFile -ErrorAction SilentlyContinue
            $preferredPort = if ($actualPort -lt 65535) { $actualPort + 1 } else { 49152 }
        }
        if (-not $ready) { throw 'Repeated port contention prevented startup. Please retry.' }
    }
    $previewUrl = "http://127.0.0.1:$($managed.port)"
    Show-ConnectionDetails -BaseUrl $previewUrl
    Show-LauncherInfo "API schema: $previewUrl/openapi.json"
    if ($PassThru) { [pscustomobject]@{ Port = $managed.port; Url = $previewUrl; ProcessId = $managed.pid; AlreadyRunning = $alreadyRunning } }
} finally { $launcherLock.Dispose() }
