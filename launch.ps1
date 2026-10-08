param(
    [ValidateSet('Start', 'Stop')][string]$Action = 'Start',
    [switch]$NoBrowser,
    [ValidateRange(1, 65535)][int]$Port = 8768,
    [switch]$LocalOnly,
    [switch]$Restart,
    [string]$DataDir = ''
)
$ErrorActionPreference = 'Stop'
try {
    if ($Action -eq 'Stop') {
        & (Join-Path $PSScriptRoot 'stop.ps1') -Port $Port
        Write-Host 'The system is stopped. You can close its browser tab.'
    } else {
        Write-Host 'Starting Chengsi. Please wait...'
        & (Join-Path $PSScriptRoot 'start.ps1') -Port $Port -LocalOnly:$LocalOnly -Restart:$Restart -DataDir $DataDir
        $launchUrl = "http://127.0.0.1:$Port/"
        $health = Invoke-RestMethod -Uri ($launchUrl + 'api/health') -TimeoutSec 5
        if ($health.service -ne 'chengsi-backend' -or $health.api_version -ne '1') {
            throw 'The backend health check did not succeed.'
        }
        if (-not $NoBrowser) { Start-Process -FilePath ($launchUrl + 'operator.html') }
        Write-Host ('System ready: ' + $launchUrl + 'operator.html')
    }
} catch {
    Write-Host ('Operation failed: ' + $_.Exception.Message) -ForegroundColor Red
    Write-Host ('See README.md and backend-server-error.log in ' + $PSScriptRoot)
    exit 1
}
