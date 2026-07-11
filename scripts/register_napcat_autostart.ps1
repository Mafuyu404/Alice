param(
    [string]$TaskName = "AliceNapCat",
    [string]$NapCatDir = "D:\program\napcat",
    [string]$NapCatWsUrl = "ws://127.0.0.1:3001",
    [string]$AliceWsUrl = "ws://127.0.0.1:58911"
)

$ErrorActionPreference = "Stop"

$scriptPath = Join-Path $PSScriptRoot "start_napcat_alice_bridge.ps1"
if (-not (Test-Path -LiteralPath $scriptPath)) {
    throw "Startup script not found: $scriptPath"
}

$arguments = @(
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-WindowStyle", "Hidden",
    "-File", "`"$scriptPath`"",
    "-NapCatDir", "`"$NapCatDir`"",
    "-NapCatWsUrl", "`"$NapCatWsUrl`"",
    "-AliceWsUrl", "`"$AliceWsUrl`""
) -join " "

$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arguments
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "Start NapCat with the embedded Alice QQ bridge." `
    -Force | Out-Null

Write-Host "Registered scheduled task: $TaskName"
