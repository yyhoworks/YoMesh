$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $taskRoot
python (Join-Path $PSScriptRoot 'yomesh.py') serve --port 8767
