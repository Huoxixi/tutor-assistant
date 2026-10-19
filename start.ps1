param([string]$PythonPath, [switch]$InstallWeChat)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$env:PYTHONUTF8 = '1'
$runtimePath = Join-Path $PSScriptRoot '.venv313\Scripts\python.exe'
function Find-ProjectPython {
    $candidates = @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python313\python.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python311\python.exe')
    )
    return $candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
}
function Install-ProjectDependencies([string]$RequirementsFile) {
    & $runtimePath -c "import importlib.util; raise SystemExit(0 if importlib.util.find_spec('pip') else 1)"
    if ($LASTEXITCODE -eq 0) {
        & $runtimePath -m pip install -r $RequirementsFile
    } else {
        $bootstrapPython = $PythonPath
        if (-not $bootstrapPython) { $bootstrapPython = Find-ProjectPython }
        if (-not $bootstrapPython) { throw 'Pass -PythonPath pointing to a working Python with pip.' }
        & $bootstrapPython -m pip --python .venv313 install -r $RequirementsFile
    }
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
}
if (-not (Test-Path -LiteralPath $runtimePath)) {
    if (-not $PythonPath) {
        $PythonPath = Find-ProjectPython
    }
    if (-not $PythonPath) {
        throw 'Please pass -PythonPath with your Python 3.11, 3.12 or 3.13 executable.'
    }
    & $PythonPath -m venv .venv313
    if ($LASTEXITCODE -ne 0 -and -not (Test-Path -LiteralPath $runtimePath)) { throw 'Could not create the isolated environment.' }
}
& $runtimePath -c "import importlib.util; raise SystemExit(0 if all(importlib.util.find_spec(m) for m in ['fastapi','uvicorn','httpx']) else 1)"
if ($LASTEXITCODE -ne 0) {
    Install-ProjectDependencies 'requirements.txt'
}
if ($InstallWeChat) {
    Install-ProjectDependencies 'requirements-wechat.txt'
}
Write-Host 'Local app: http://127.0.0.1:8765'
Write-Host 'Stop with Ctrl+C. Original messages stay in data/tutor.sqlite3.'
& $runtimePath run.py
