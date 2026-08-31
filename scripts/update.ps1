# Pull the latest image built from main and restart. Run this on the pool device.
# Deliberately manual: never auto-update during a meet.
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
Write-Host "==> pulling"
docker compose pull
Write-Host "==> restarting"
docker compose up -d
Write-Host "==> waiting for health"
for ($i = 0; $i -lt 20; $i++) {
    $s = docker inspect -f '{{.State.Health.Status}}' meet-board 2>$null
    if ($s -eq "healthy") { Write-Host "healthy"; docker compose ps; exit 0 }
    Start-Sleep -Seconds 2
}
Write-Host "did not become healthy - check: docker compose logs --tail=50"
exit 1
