# Starts the CPM API on http://localhost:8010 (reads backend/.env for the database etc.)
#
# Which model answers is chosen per project in the chat (configured under Settings -> Models).
# -Llm only sets the fallback for projects/organizations with no model configured:
#   .\dev-backend.ps1                 # fallback: local Ollama (qwen3:8b)
#   .\dev-backend.ps1 -Llm anthropic  # fallback: Claude (needs ANTHROPIC_API_KEY)
#   .\dev-backend.ps1 -Llm stub       # force the offline placeholder everywhere (no model at all)
param([ValidateSet("ollama", "anthropic", "stub")][string]$Llm = "ollama")

$host.UI.RawUI.WindowTitle = "CPM API :8010 ($Llm)"
Set-Location (Join-Path $PSScriptRoot "backend")
$env:CPM_LLM_PROVIDER = $Llm
$env:CPM_STUB_LLM = $(if ($Llm -eq "stub") { "1" } else { "0" })
$env:PYTHONIOENCODING = "utf-8"
if ($Llm -eq "ollama") {
    try { Invoke-RestMethod http://localhost:11434/api/version -TimeoutSec 3 | Out-Null }
    catch { Write-Host "Ollama is not running - start it (Ollama app) or the local model will be unavailable." -ForegroundColor Yellow }
}
Write-Host "CPM API on http://localhost:8010 (fallback model: $Llm). Press Ctrl+C to stop." -ForegroundColor Cyan
& .\.venv\Scripts\python.exe -m uvicorn app.main:app --port 8010
