<#
  One-command local run with a real Telegram webhook (Windows / PowerShell):
    1. starts a Cloudflare quick tunnel to localhost
    2. writes the public https URL into .env as WEBHOOK_URL
    3. starts the bot with docker compose

  Requires: Docker Desktop, cloudflared
            (winget install --id Cloudflare.cloudflared)

  Run from the project root:   .\scripts\dev_tunnel.ps1
  If PowerShell blocks the script, allow it for this session first:
            Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
#>

$ErrorActionPreference = 'Stop'

# move to the project root (parent of this script's folder)
Set-Location (Split-Path $PSScriptRoot -Parent)

$port = if ($env:PORT) { $env:PORT } else { '8080' }

if (-not (Test-Path '.env')) {
    Write-Host "No .env found - run: Copy-Item .env.example .env  (then fill BOT_TOKEN)"
    exit 1
}

if (-not (Get-Command cloudflared -ErrorAction SilentlyContinue)) {
    Write-Host "cloudflared not found. Install it with:"
    Write-Host "    winget install --id Cloudflare.cloudflared"
    exit 1
}

$log = New-TemporaryFile
Write-Host "> Starting Cloudflare quick tunnel -> http://localhost:$port ..."

# start cloudflared in the background, capturing its output to the log file
$cf = Start-Process -FilePath 'cloudflared' `
    -ArgumentList @('tunnel', '--url', "http://localhost:$port") `
    -RedirectStandardOutput $log.FullName `
    -RedirectStandardError  "$($log.FullName).err" `
    -NoNewWindow -PassThru

# always stop the tunnel when this script exits (Ctrl+C included)
function Stop-Tunnel {
    if ($cf -and -not $cf.HasExited) { Stop-Process -Id $cf.Id -Force -ErrorAction SilentlyContinue }
}

try {
    # poll both log files for the assigned https://*.trycloudflare.com URL
    $url = $null
    foreach ($i in 1..30) {
        $text = @()
        if (Test-Path $log.FullName)          { $text += Get-Content $log.FullName -Raw -ErrorAction SilentlyContinue }
        if (Test-Path "$($log.FullName).err") { $text += Get-Content "$($log.FullName).err" -Raw -ErrorAction SilentlyContinue }
        $m = [regex]::Match(($text -join "`n"), 'https://[a-z0-9-]+\.trycloudflare\.com')
        if ($m.Success) { $url = $m.Value; break }
        Start-Sleep -Seconds 1
    }

    if (-not $url) {
        Write-Host "x Tunnel did not come up. Log:"
        if (Test-Path $log.FullName)          { Get-Content $log.FullName }
        if (Test-Path "$($log.FullName).err") { Get-Content "$($log.FullName).err" }
        Stop-Tunnel
        exit 1
    }
    Write-Host "v Tunnel ready: $url"

    # write WEBHOOK_URL into .env (replace existing line or append)
    $lines = Get-Content '.env'
    if ($lines -match '^WEBHOOK_URL=') {
        $lines = $lines -replace '^WEBHOOK_URL=.*', "WEBHOOK_URL=$url"
        Set-Content '.env' $lines -Encoding utf8
    } else {
        Add-Content '.env' "WEBHOOK_URL=$url" -Encoding utf8
    }
    Write-Host "v .env updated (WEBHOOK_URL=$url)"

    Write-Host "> Starting the bot (Ctrl+C stops bot + tunnel)..."
    docker compose up --build
}
finally {
    Stop-Tunnel
    Remove-Item $log.FullName, "$($log.FullName).err" -ErrorAction SilentlyContinue
}
