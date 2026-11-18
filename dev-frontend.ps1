# Starts the CPM web app on http://localhost:3010 (production build: light on memory).
# Usage:  .\dev-frontend.ps1           # serve the existing build
#         .\dev-frontend.ps1 -Build    # rebuild first (after code changes)
param([switch]$Build)

$host.UI.RawUI.WindowTitle = "CPM UI :3010"
Set-Location (Join-Path $PSScriptRoot "frontend")
if ($Build -or -not (Test-Path ".next\BUILD_ID")) {
    Write-Host "Building the web app..." -ForegroundColor Cyan
    npm run build
    if ($LASTEXITCODE -ne 0) { Write-Host "Build failed." -ForegroundColor Red; exit 1 }
}
Write-Host "CPM UI on http://localhost:3010 - sign in as owner@demo.local. Press Ctrl+C to stop." -ForegroundColor Cyan
node node_modules/next/dist/bin/next start -p 3010
