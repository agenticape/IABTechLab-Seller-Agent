<#
.SYNOPSIS
  Run the mock-buyer scenarios against a running sandbox seller.
  Arguments are passed through to sandbox/scenarios.py.

.EXAMPLE
  .\sandbox\scenarios.ps1
.EXAMPLE
  .\sandbox\scenarios.ps1 --only 3,4 --verbose
.EXAMPLE
  .\sandbox\scenarios.ps1 --buyer-agent ..\buyer-agent
#>
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    $env:Path = (Join-Path $env:USERPROFILE '.local\bin') + ";$env:Path"
}
uv run --locked --python 3.12 python sandbox/scenarios.py @args
exit $LASTEXITCODE
