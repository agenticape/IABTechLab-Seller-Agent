<#
.SYNOPSIS
  One step: re-run the idempotent sandbox setup, then start the mock-only seller
  on http://localhost:8000. Ctrl+C stops it.

.EXAMPLE
  .\sandbox\run.ps1
.EXAMPLE
  .\sandbox\run.ps1 -SkipSetup     # start only
.EXAMPLE
  .\sandbox\run.ps1 -Reload        # auto-reload on code edits
#>
[CmdletBinding()]
param(
    [switch]$SkipSetup,
    [switch]$Reload
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

if (-not $SkipSetup) {
    & (Join-Path $PSScriptRoot 'setup.ps1')
}
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    $env:Path = (Join-Path $env:USERPROFILE '.local\bin') + ";$env:Path"
}

$ServerArgs = @()
if ($Reload) { $ServerArgs += '--reload' }

Write-Host ''
Write-Host '==> Starting the sandbox seller (Ctrl+C to stop)' -ForegroundColor Cyan
uv run --locked --python 3.12 python sandbox/run_server.py @ServerArgs
exit $LASTEXITCODE
