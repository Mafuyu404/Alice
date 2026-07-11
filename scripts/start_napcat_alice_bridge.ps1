param(
    [string]$NapCatDir = "D:\program\napcat",
    [string]$NapCatWsUrl = "ws://127.0.0.1:3001",
    [string]$AliceWsUrl = "ws://127.0.0.1:58911",
    [string]$LogDir = ""
)

$ErrorActionPreference = "Stop"

$NapCatDir = (Resolve-Path -LiteralPath $NapCatDir).Path
$nodePath = Join-Path $NapCatDir "node.exe"
$indexPath = Join-Path $NapCatDir "index.js"

if (-not (Test-Path -LiteralPath $nodePath)) {
    throw "NapCat node.exe not found: $nodePath"
}
if (-not (Test-Path -LiteralPath $indexPath)) {
    throw "NapCat index.js not found: $indexPath"
}

if (-not $LogDir.Trim()) {
    $LogDir = Join-Path $PSScriptRoot "..\logs\napcat"
}
$LogDir = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($LogDir)
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$env:NAPCAT_WS_URL = $NapCatWsUrl
$env:ALICE_WS_URL = $AliceWsUrl

$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$stdout = Join-Path $LogDir "napcat-alice-$stamp.out.log"
$stderr = Join-Path $LogDir "napcat-alice-$stamp.err.log"

Start-Process `
    -FilePath $nodePath `
    -ArgumentList @($indexPath) `
    -WorkingDirectory $NapCatDir `
    -RedirectStandardOutput $stdout `
    -RedirectStandardError $stderr `
    -WindowStyle Hidden
