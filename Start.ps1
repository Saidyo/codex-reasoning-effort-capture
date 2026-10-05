param(
    [int]$Port = 18080,
    [double]$Seconds = 300,
    [int]$Count = 0,
    [string]$ThreadId = '',
    [switch]$Doctor,
    [switch]$UI
)
$ErrorActionPreference = 'Stop'
$venvPython = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $venvPython)) {
    & python -m venv (Join-Path $PSScriptRoot '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Could not create Python virtual environment.' }
}
& $venvPython -c "import importlib.util, importlib.metadata, sys; sys.exit(0 if importlib.util.find_spec('dpkt') and importlib.metadata.version('dpkt') == '1.9.8' else 1)"
if ($LASTEXITCODE -ne 0) {
    & $venvPython -m pip install --disable-pip-version-check --timeout 60 --retries 2 -r (Join-Path $PSScriptRoot 'requirements.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Could not install capture dependencies.' }
}
$captureArgs = @((Join-Path $PSScriptRoot 'capture.py'), '--port', $Port, '--seconds', $Seconds, '--count', $Count)
if ($ThreadId) { $captureArgs += @('--thread-id', $ThreadId) }
if ($Doctor) { $captureArgs += '--doctor' }
if ($UI) { $captureArgs = @((Join-Path $PSScriptRoot 'dashboard.py'), '--capture-port', $Port, '--open') }
& $venvPython @captureArgs
exit $LASTEXITCODE
