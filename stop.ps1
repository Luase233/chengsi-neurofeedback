param([int]$Port = 8768)
$ErrorActionPreference = 'Stop'
$pidFile = Join-Path $PSScriptRoot 'preview-server.pid'
if (-not (Test-Path -LiteralPath $pidFile)) { Write-Output 'No managed server.'; return }
$previewProcessId = [int](Get-Content -LiteralPath $pidFile -Raw).Trim()
$serverScript = Join-Path $PSScriptRoot 'server.py'
$previewProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $previewProcessId" -ErrorAction SilentlyContinue
if ($previewProcess) {
    if ($previewProcess.CommandLine -notmatch [regex]::Escape($serverScript)) {
        throw 'The saved process id belongs to a different command. No process was stopped.'
    }
    $listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if ($listener -and $listener.OwningProcess -notcontains $previewProcessId) {
        $listenerIds = @($listener | Select-Object -ExpandProperty OwningProcess -Unique)
        if ($listenerIds.Count -ne 1) { throw 'The requested port has unexpected owners. No process was stopped.' }
        $listenerProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $($listenerIds[0])"
        if ($listenerProcess.ParentProcessId -ne $previewProcessId -or $listenerProcess.CommandLine -notmatch [regex]::Escape($serverScript)) {
            throw 'The requested port does not belong to the managed process. Check -Port before stopping.'
        }
        $previewProcessId = [int]$listenerProcess.ProcessId
    }
    $current = $null
    try { $current = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/state" -TimeoutSec 3 } catch {}
    if ($current.session_id -and $current.phase -notin @('completed', 'idle')) {
        $body = @{ command = 'finish'; command_id = [guid]::NewGuid().ToString() } | ConvertTo-Json
        Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/sessions/$($current.session_id)/commands" -Method Post -ContentType 'application/json' -Body $body -TimeoutSec 10 | Out-Null
    }
    Stop-Process -Id $previewProcessId
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        if (-not (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)) { break }
        Start-Sleep -Milliseconds 100
    }
}
Remove-Item -LiteralPath $pidFile
Write-Output 'Local server stopped.'
