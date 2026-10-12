# Shared by the Windows launchers. A health response never establishes ownership.
function Get-ChengsiArguments {
    param([string]$CommandLine, [string]$ExecutablePath)
    $commandTokens = @([regex]::Matches($CommandLine, '"[^"]*"|\S+') | ForEach-Object { $_.Value.Trim('"') })
    $knownInterpreters = @((Join-Path $demoRoot '.venv\Scripts\python.exe'))
    if (-not [string]::IsNullOrWhiteSpace($ExecutablePath)) { $knownInterpreters += $ExecutablePath }
    $interpreterMatches = @($knownInterpreters | Where-Object {
        [string]::Equals($_, $commandTokens[0], [StringComparison]::OrdinalIgnoreCase)
    })
    if ($commandTokens.Count -lt 3 -or -not [IO.Path]::IsPathRooted($commandTokens[0]) -or $interpreterMatches.Count -eq 0) {
        throw 'The saved PID belongs to another program or project. No process was reused or stopped.'
    }
    # Only the launcher's supported flags may precede its first script argument.
    if ($commandTokens[1] -eq '-u') { $scriptIndex = 2 }
    elseif ($commandTokens.Count -ge 5 -and $commandTokens[1] -eq '-X' -and
            $commandTokens[2] -eq 'utf8' -and $commandTokens[3] -eq '-u') { $scriptIndex = 4 }
    else { throw 'The saved PID belongs to another program or project. No process was reused or stopped.' }
    if ($scriptIndex -ge $commandTokens.Count -or -not [string]::Equals($commandTokens[$scriptIndex], $serverScript, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'The saved PID belongs to another program or project. No process was reused or stopped.'
    }
    if ($scriptIndex + 1 -ge $commandTokens.Count) { return @() }
    return $commandTokens[($scriptIndex + 1)..($commandTokens.Count - 1)]
}
function Get-ChengsiProcess {
    param([int]$ProcessId)
    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction SilentlyContinue
    if (-not $process) { return $null }
    if ($process.Name -notmatch '^python(?:w|3(?:\.\d+)?)?\.exe$') {
        throw 'The saved PID belongs to another program or project. No process was reused or stopped.'
    }
    Get-ChengsiArguments $process.CommandLine $process.ExecutablePath | Out-Null
    return $process
}
function Get-ChengsiPort {
    param([string]$CommandLine, [string]$ExecutablePath)
    $launchArguments = @(Get-ChengsiArguments $CommandLine $ExecutablePath)
    $portIndex = [Array]::IndexOf($launchArguments, '--port')
    if ($portIndex -lt 0 -or $portIndex + 1 -ge $launchArguments.Count -or $launchArguments[$portIndex + 1] -notmatch '^\d+$') { throw 'The managed process has no valid port argument.' }
    $actualPort = [int]$launchArguments[$portIndex + 1]
    if ($actualPort -lt 1 -or $actualPort -gt 65535) { throw 'The managed process has an invalid port.' }
    return $actualPort
}
function Get-ChengsiCreatedAt {
    param($Process)
    return $Process.CreationDate.ToUniversalTime().ToString('o')
}
function Get-ChengsiListener {
    param([int]$ProcessId, [int]$ActualPort)
    $owned = @()
    foreach ($ownerId in @(Get-NetTCPConnection -LocalPort $ActualPort -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique)) {
        $candidate = Get-CimInstance Win32_Process -Filter "ProcessId = $ownerId" -ErrorAction SilentlyContinue
        if ($candidate -and ($candidate.ProcessId -eq $ProcessId -or $candidate.ParentProcessId -eq $ProcessId)) {
            $candidate = Get-ChengsiProcess -ProcessId $ownerId
            if ((Get-ChengsiPort $candidate.CommandLine $candidate.ExecutablePath) -eq $ActualPort) { $owned += $candidate }
        }
    }
    if ($owned.Count -gt 1) { throw 'More than one managed listener was found. No process was stopped.' }
    if ($owned.Count -eq 1) { return $owned[0] }
    return $null
}
function Get-ChengsiManagedServer {
    if (-not (Test-Path -LiteralPath $managedPidFile)) { return $null }
    $savedId = (Get-Content -LiteralPath $managedPidFile -Raw).Trim()
    if ($savedId -notmatch '^\d+$') { throw 'Invalid saved PID. No process was reused or stopped.' }
    $process = Get-ChengsiProcess -ProcessId ([int]$savedId)
    if (-not $process) { return $null }
    $actualPort = Get-ChengsiPort $process.CommandLine $process.ExecutablePath
    if (Test-Path -LiteralPath $managedStateFile) {
        $saved = Get-Content -LiteralPath $managedStateFile -Raw | ConvertFrom-Json
        if ($saved.pid -ne $process.ProcessId -or $saved.root -ne $demoRoot -or $saved.server_script -ne $serverScript -or
            $saved.port -ne $actualPort -or ([datetime]$saved.started_at).ToUniversalTime().Ticks -ne $process.CreationDate.ToUniversalTime().Ticks) {
            throw 'The managed state does not match the current process. No process was reused or stopped.'
        }
    }
    # A Windows venv launcher may have a base-Python child owning the socket.
    $listener = Get-ChengsiListener -ProcessId $process.ProcessId -ActualPort $actualPort
    if ($listener) { $process = $listener }
    $dataPath = Join-Path $demoRoot 'runtime\sessions'
    $launchArguments = @(Get-ChengsiArguments $process.CommandLine $process.ExecutablePath)
    $dataIndex = [Array]::IndexOf($launchArguments, '--data-dir')
    if ($dataIndex -ge 0 -and $dataIndex + 1 -lt $launchArguments.Count) { $dataPath = $launchArguments[$dataIndex + 1] }
    return [pscustomobject]@{
        pid = [int]$process.ProcessId; port = $actualPort; root = $demoRoot; server_script = $serverScript
        started_at = Get-ChengsiCreatedAt $process; data_dir = [IO.Path]::GetFullPath($dataPath)
        lan_enabled = [bool]($launchArguments -contains '--lan')
    }
}
function Save-ChengsiManagedServer {
    param($Server)
    $Server | ConvertTo-Json | Set-Content -LiteralPath ($managedStateFile + '.tmp') -Encoding UTF8
    Move-Item -LiteralPath ($managedStateFile + '.tmp') -Destination $managedStateFile -Force
    Set-Content -LiteralPath $managedPidFile -Value $Server.pid -Encoding ASCII
}
function Enter-ChengsiLauncherLock {
    $lockPath = Join-Path $demoRoot 'runtime\preview-server.lock'
    New-Item -ItemType Directory -Path (Split-Path -Parent $lockPath) -Force | Out-Null
    for ($lockAttempt = 0; $lockAttempt -lt 100; $lockAttempt++) {
        try { return [IO.File]::Open($lockPath, 'OpenOrCreate', 'ReadWrite', 'None') }
        catch [IO.IOException] { Start-Sleep -Milliseconds 100 }
    }
    throw 'Another launcher is busy. Wait for it to finish, then retry.'
}
function Get-ChengsiAvailablePort {
    param([int]$PreferredPort, [bool]$LanEnabled)
    $address = if ($LanEnabled) { [Net.IPAddress]::Any } else { [Net.IPAddress]::Loopback }
    $candidates = @($PreferredPort..([Math]::Min(65535, $PreferredPort + 99))) + @(0)
    foreach ($candidate in $candidates) {
        $probe = New-Object Net.Sockets.TcpListener($address, $candidate)
        $probe.Server.ExclusiveAddressUse = $true
        try {
            $probe.Start()
            return ([Net.IPEndPoint]$probe.LocalEndpoint).Port
        } catch [Net.Sockets.SocketException] {
            # Keep the occupied owner untouched and try the next candidate.
        } finally { $probe.Stop() }
    }
    throw 'Could not find an available local port.'
}
