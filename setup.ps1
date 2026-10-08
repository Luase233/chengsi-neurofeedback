param([string]$Python = '')
$ErrorActionPreference = 'Stop'
$demoRoot = $PSScriptRoot
$venvPython = Join-Path $demoRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $venvPython)) {
    $pythonArgs = @()
    if ($Python) { $basePython = (Get-Command $Python -ErrorAction Stop).Source }
    elseif (Get-Command py -ErrorAction SilentlyContinue) {
        $basePython = (Get-Command py).Source
        $pythonArgs = @('-3.12')
    } elseif (Get-Command python -ErrorAction SilentlyContinue) {
        $basePython = (Get-Command python).Source
    } else { throw 'Install 64-bit Python 3.12, then run .\setup.ps1 -Python <python.exe path>.' }
    & $basePython @pythonArgs -c 'import sys; assert sys.version_info[:2] == (3, 12) and sys.maxsize > 2**32, "Use 64-bit Python 3.12 for the locked Windows environment"'
    if ($LASTEXITCODE -ne 0) { throw 'A working 64-bit Python 3.12 is required.' }
    & $basePython @pythonArgs -m venv (Join-Path $demoRoot '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the project environment.' }
}
& $venvPython -c 'import sys; assert sys.version_info[:2] == (3, 12) and sys.maxsize > 2**32, "Recreate .venv with 64-bit Python 3.12"'
if ($LASTEXITCODE -ne 0) { throw 'The existing environment requires 64-bit Python 3.12.' }
& $venvPython -m pip install -r (Join-Path $demoRoot 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
& $venvPython -X utf8 -c 'from brainflow.board_shim import BoardShim, BoardIds; import fastapi, uvicorn, scipy, bleak, pyedflib; print(BoardShim.get_board_descr(BoardIds.MUSE_2_BOARD))'
if ($LASTEXITCODE -ne 0) { throw 'Backend import/native-library check failed.' }
Write-Output 'Environment ready. Run .\launch.ps1 (LAN mode), or .\launch.ps1 -LocalOnly.'
