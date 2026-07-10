param(
    [string]$NapCatDir = "D:\program\napcat",
    [string]$ProjectRoot = "",
    [string]$NapCatWsUrl = "ws://127.0.0.1:3001",
    [string]$AliceWsUrl = "ws://127.0.0.1:58911",
    [string]$QQ = "",
    [string]$Python = "python",
    [int]$PollSeconds = 3,
    [int]$LoginTimeoutSeconds = 0,
    [switch]$DebugBridge,
    [switch]$NoStartNapCat
)

$ErrorActionPreference = "Stop"

function Resolve-ProjectRoot {
    param([string]$Value)
    if ($Value.Trim()) {
        return (Resolve-Path -LiteralPath $Value).Path
    }
    return (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
}

function Get-WsEndpoint {
    param([string]$Url)
    $uri = [Uri]$Url
    $port = $uri.Port
    if ($port -lt 0) {
        if ($uri.Scheme -eq "wss") {
            $port = 443
        } else {
            $port = 80
        }
    }
    return @{ Host = $uri.Host; Port = $port }
}

function Test-TcpPort {
    param([string]$HostName, [int]$Port)
    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $async = $client.BeginConnect($HostName, $Port, $null, $null)
        if (-not $async.AsyncWaitHandle.WaitOne(800)) {
            return $false
        }
        $client.EndConnect($async)
        return $true
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

function Test-NapCatLogin {
    param([string]$Url, [string]$PythonExe)
    $code = @'
import asyncio
import json
import sys

async def main():
    url = sys.argv[1]
    try:
        import websockets
        async with websockets.connect(url, open_timeout=2, close_timeout=1) as ws:
            await ws.send(json.dumps({"action": "get_login_info", "params": {}, "echo": "alice_login_check"}))
            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=3)
                data = json.loads(raw)
                if data.get("echo") != "alice_login_check":
                    continue
                if data.get("status") == "ok" and int(data.get("retcode", -1)) == 0:
                    info = data.get("data") or {}
                    user_id = info.get("user_id") or info.get("uin") or ""
                    nickname = info.get("nickname") or ""
                    print(f"logged_in {user_id} {nickname}".strip())
                    return 0
                print(f"not_logged_in retcode={data.get('retcode')} status={data.get('status')}")
                return 2
    except Exception as exc:
        print(f"unavailable {type(exc).__name__}: {exc}")
        return 1

raise SystemExit(asyncio.run(main()))
'@
    $output = $code | & $PythonExe - $Url 2>&1
    $exit = $LASTEXITCODE
    return @{ ExitCode = $exit; Output = ($output -join "`n") }
}

$ProjectRoot = Resolve-ProjectRoot $ProjectRoot
$NapCatDir = (Resolve-Path -LiteralPath $NapCatDir).Path
$nodePath = Join-Path $NapCatDir "node.exe"
$indexPath = Join-Path $NapCatDir "index.js"
$launcherPath = Join-Path $NapCatDir "napcat\launcher-user.bat"
$bridgePath = Join-Path $ProjectRoot "qq_client.py"

if (-not (Test-Path -LiteralPath $nodePath)) {
    throw "NapCat node.exe not found: $nodePath"
}
if (-not (Test-Path -LiteralPath $indexPath)) {
    throw "NapCat index.js not found: $indexPath"
}
if ($QQ.Trim() -and -not (Test-Path -LiteralPath $launcherPath)) {
    throw "NapCat quick-login launcher not found: $launcherPath"
}
if (-not (Test-Path -LiteralPath $bridgePath)) {
    throw "Alice QQ bridge not found: $bridgePath"
}

$endpoint = Get-WsEndpoint $NapCatWsUrl
$portOpen = Test-TcpPort -HostName $endpoint.Host -Port $endpoint.Port

if (-not $NoStartNapCat -and -not $portOpen) {
    if ($QQ.Trim()) {
        $launcherDir = Split-Path -Parent $launcherPath
        Write-Host "[qq-stack] starting NapCat quick login for QQ $QQ"
        Write-Host "[qq-stack] if quick login fails or session expired, scan the QR code in the NapCat window."
        Start-Process -FilePath "cmd.exe" -ArgumentList @("/k", "`"$launcherPath`" $QQ") -WorkingDirectory $launcherDir
    } else {
        Write-Host "[qq-stack] starting NapCat: $indexPath"
        Write-Host "[qq-stack] if login expired, scan the QR code in the NapCat window."
        Start-Process -FilePath $nodePath -ArgumentList @($indexPath) -WorkingDirectory $NapCatDir
    }
} elseif ($portOpen) {
    Write-Host "[qq-stack] NapCat websocket port is already open: $NapCatWsUrl"
} else {
    Write-Host "[qq-stack] NoStartNapCat is set; waiting for existing NapCat: $NapCatWsUrl"
}

$startedAt = Get-Date
$reportedQrHint = $false
while ($true) {
    $login = Test-NapCatLogin -Url $NapCatWsUrl -PythonExe $Python
    if ($login.ExitCode -eq 0) {
        Write-Host "[qq-stack] NapCat login ready: $($login.Output)"
        break
    }
    if (-not $reportedQrHint) {
        Write-Host "[qq-stack] NapCat is not logged in or not ready yet."
        Write-Host "[qq-stack] keep the NapCat window open; scan its QR code if it appears."
        $reportedQrHint = $true
    } else {
        Write-Host "[qq-stack] waiting for NapCat login... $($login.Output)"
    }
    if ($LoginTimeoutSeconds -gt 0) {
        $elapsed = ((Get-Date) - $startedAt).TotalSeconds
        if ($elapsed -ge $LoginTimeoutSeconds) {
            throw "NapCat login was not ready within $LoginTimeoutSeconds seconds."
        }
    }
    Start-Sleep -Seconds ([Math]::Max(1, $PollSeconds))
}

Write-Host "[qq-stack] starting Alice QQ bridge:"
Write-Host "[qq-stack]   NapCat: $NapCatWsUrl"
Write-Host "[qq-stack]   Alice : $AliceWsUrl"
Set-Location -LiteralPath $ProjectRoot
$bridgeArgs = @($bridgePath, "--ws", $NapCatWsUrl, "--alice", $AliceWsUrl)
if ($DebugBridge) {
    $bridgeArgs += "--debug"
}
& $Python @bridgeArgs
