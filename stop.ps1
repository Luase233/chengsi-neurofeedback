param([ValidateRange(1, 65535)][int]$Port = 8768)
$ErrorActionPreference = 'Stop'
$demoRoot = [IO.Path]::GetFullPath($PSScriptRoot)
$serverScript = Join-Path $demoRoot 'server.py'
$managedPidFile = Join-Path $demoRoot 'preview-server.pid'
$managedStateFile = Join-Path $demoRoot 'runtime\preview-server.json'
. (Join-Path $demoRoot 'launcher-process.ps1')
$launcherLock = Enter-ChengsiLauncherLock
try {
    $managed = Get-ChengsiManagedServer
    if ($managed) {
        if ($PSBoundParameters.ContainsKey('Port') -and $Port -ne $managed.port) { throw "The requested port does not match this project's managed server. No process was stopped." }
        $actualPort = $managed.port
        # Send finish only when this project's process owns the actual listener.
        if (Get-ChengsiListener -ProcessId $managed.pid -ActualPort $actualPort) {
            $current = $null
            try { $current = Invoke-RestMethod -Uri "http://127.0.0.1:$actualPort/api/state" -TimeoutSec 3 } catch {}
            if ($current.session_id -and $current.phase -notin @('completed', 'idle', 'error')) {
                $body = @{ command = 'finish'; command_id = [guid]::NewGuid().ToString() } | ConvertTo-Json
                Invoke-RestMethod -Uri "http://127.0.0.1:$actualPort/api/sessions/$($current.session_id)/commands" -Method Post -ContentType 'application/json' -Body $body -TimeoutSec 10 | Out-Null
            }
        }
        $process = Get-ChengsiProcess -ProcessId $managed.pid
        if ($process -and (Get-ChengsiCreatedAt $process) -eq $managed.started_at) { Stop-Process -Id $managed.pid }
        for ($attempt = 0; $attempt -lt 30; $attempt++) {
            if (-not (Get-Process -Id $managed.pid -ErrorAction SilentlyContinue)) { break }
            Start-Sleep -Milliseconds 100
        }
        if (Get-Process -Id $managed.pid -ErrorAction SilentlyContinue) { throw 'Shutdown is still in progress. The managed state was kept; retry shortly.' }
        Write-Output "Managed server stopped (port $actualPort)."
    } else { Write-Output 'No running managed server.' }
    foreach ($file in @($managedPidFile, $managedStateFile)) { if (Test-Path -LiteralPath $file) { Remove-Item -LiteralPath $file } }
} finally { $launcherLock.Dispose() }
