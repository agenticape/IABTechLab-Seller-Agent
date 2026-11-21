<#
.SYNOPSIS
  Mock-only seller sandbox setup for Windows PowerShell 5.1 / PowerShell 7. Safe to re-run.

.DESCRIPTION
  Installs uv (if missing), Python 3.12 (managed by uv), the locked dependencies,
  copies sandbox/.env.sandbox to sandbox/.local/sandbox.env, and mints an operator
  key plus fictional buyer keys into sandbox/.local/keys.json (git-ignored).

.EXAMPLE
  .\sandbox\setup.ps1
.EXAMPLE
  .\sandbox\setup.ps1 -Rekey     # revoke and re-mint the API keys
.EXAMPLE
  .\sandbox\setup.ps1 -Reset     # delete the sandbox DB (stop the server first), then re-mint keys
#>
[CmdletBinding()]
param(
    [switch]$Rekey,
    [switch]$Reset
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$PythonVersion = '3.12'
$LocalDir = Join-Path $RepoRoot 'sandbox\.local'

function Write-Step([string]$Message) {
    Write-Host ''
    Write-Host "==> $Message" -ForegroundColor Cyan
}

# Native commands don't throw on failure in Windows PowerShell 5.1; check the exit code.
function Invoke-Native([string]$Description, [scriptblock]$Command) {
    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw "$Description failed (exit code $LASTEXITCODE)."
    }
}

if ($Reset) {
    Remove-Item -Force -ErrorAction SilentlyContinue (Join-Path $LocalDir 'ad_seller_sandbox.db*')
    Remove-Item -Force -ErrorAction SilentlyContinue (Join-Path $LocalDir 'keys.json')
    Write-Host 'Sandbox database and keys deleted.'
}

Write-Step 'Checking for uv'
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    $UserBin = Join-Path $env:USERPROFILE '.local\bin'
    if (Test-Path (Join-Path $UserBin 'uv.exe')) {
        $env:Path = "$UserBin;$env:Path"
    }
    else {
        Write-Host 'uv not found; installing it from https://astral.sh/uv (per-user, no admin rights).'
        try {
            powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
        }
        catch {
            Write-Host 'Automatic install failed. Install uv yourself, open a NEW terminal, and re-run:' -ForegroundColor Yellow
            Write-Host '  winget install --id=astral-sh.uv -e'
            Write-Host '  or see https://docs.astral.sh/uv/getting-started/installation/'
            throw
        }
        $env:Path = "$UserBin;$env:Path"
        if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
            throw 'uv was installed but is not on PATH. Open a new terminal and re-run this script.'
        }
    }
}
Invoke-Native 'uv --version' { uv --version }

Write-Step "Installing Python $PythonVersion (managed by uv; the system Python is not used)"
Invoke-Native 'uv python install' { uv python install $PythonVersion }

Write-Step 'Syncing locked dependencies into .venv'
Invoke-Native 'uv sync' { uv sync --locked --python $PythonVersion --extra dev }

Write-Step 'Sandbox config'
New-Item -ItemType Directory -Force -Path $LocalDir | Out-Null
$LocalEnv = Join-Path $LocalDir 'sandbox.env'
if (Test-Path $LocalEnv) {
    Write-Host 'Keeping existing sandbox\.local\sandbox.env'
}
else {
    Copy-Item (Join-Path $RepoRoot 'sandbox\.env.sandbox') $LocalEnv
    Write-Host 'Created sandbox\.local\sandbox.env from sandbox\.env.sandbox'
}

Write-Step 'API keys'
$MintArgs = @()
if ($Rekey) { $MintArgs += '--force' }
Invoke-Native 'mint_keys.py' { uv run --locked --python $PythonVersion python sandbox/mint_keys.py @MintArgs }

Write-Host ''
Write-Host 'Sandbox ready (mock-only: CSV catalog, local SQLite, no ad server/SSP/registry).' -ForegroundColor Green
Write-Host '  Start the seller:   .\sandbox\run.ps1'
Write-Host '  Run the scenarios:  .\sandbox\scenarios.ps1        (in a second terminal)'
Write-Host '  Your config:        sandbox\.local\sandbox.env  (git-ignored; add ANTHROPIC_API_KEY here)'
Write-Host '  Your API keys:      sandbox\.local\keys.json    (git-ignored)'
