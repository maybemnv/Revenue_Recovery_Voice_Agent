[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker Desktop with Compose is required."
}

$escapedRoot = $Root.Replace("'", "''")
$command = "`$Host.UI.RawUI.WindowTitle = 'Revenue Recovery Voice Agent'; Set-Location -LiteralPath '$escapedRoot'; docker compose --profile fixture up --build"
Start-Process -FilePath "powershell.exe" -ArgumentList @(
    "-NoExit", "-ExecutionPolicy", "Bypass", "-Command", $command
)

Write-Host "Revenue Recovery Voice Agent starting at http://127.0.0.1:3101"
Write-Host "API health: http://127.0.0.1:8101/health"
