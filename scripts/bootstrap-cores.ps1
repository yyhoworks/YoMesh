$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskLock = Get-Content -LiteralPath (Join-Path $taskRoot 'tools/cores.lock.json') -Raw | ConvertFrom-Json
foreach ($taskItem in $taskLock.cores.PSObject.Properties) {
    $taskName = $taskItem.Name
    $taskSpec = $taskItem.Value
    $taskDestination = Join-Path $taskRoot ('tools/cores/' + $taskName)
    if (Test-Path -LiteralPath $taskDestination) {
        Write-Host ($taskName + ': already present; existing files preserved')
        continue
    }
    $taskDownloadDir = Join-Path $taskRoot 'work/downloads'
    New-Item -ItemType Directory -Path $taskDownloadDir -Force | Out-Null
    $taskArchive = Join-Path $taskDownloadDir ($taskName + '.zip')
    Invoke-WebRequest -Uri $taskSpec.url -OutFile $taskArchive
    $taskHash = (Get-FileHash -LiteralPath $taskArchive -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($taskHash -ne $taskSpec.sha256) { throw ($taskName + ': SHA256 mismatch; not extracted') }
    Expand-Archive -LiteralPath $taskArchive -DestinationPath $taskDestination
    Write-Host ($taskName + ' ' + $taskSpec.version + ': verified')
}
