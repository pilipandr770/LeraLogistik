# start.ps1 — local development startup script
#
# Usage:
#   .\start.ps1           — run with default settings from .env
#   .\start.ps1 --port 8080
#
# What it does:
#   1. Checks that .env exists (won't run without secrets)
#   2. Creates/activates Python virtual environment
#   3. Installs/updates dependencies from pyproject.toml
#   4. Runs Alembic migrations (creates schema + all tables if new)
#   5. Starts uvicorn with hot-reload
#
# First-time setup:
#   Copy .env.example to .env and fill in your DATABASE_URL and API keys.
#   For Render DB: paste the "External Database URL" from Render dashboard.

param(
    [int]$Port = 8000,
    [switch]$NoReload
)

$ErrorActionPreference = "Stop"

# ── 1. Sanity checks ────────────────────────────────────────────────────────
if (-not (Test-Path ".env")) {
    Write-Host ""
    Write-Host "ERROR: .env file not found!" -ForegroundColor Red
    Write-Host "Copy .env.example to .env and fill in your secrets:" -ForegroundColor Yellow
    Write-Host "  Copy-Item .env.example .env" -ForegroundColor Cyan
    Write-Host ""
    exit 1
}

# ── 2. Virtual environment ──────────────────────────────────────────────────
if (-not (Test-Path ".venv")) {
    Write-Host "Creating virtual environment..." -ForegroundColor Cyan
    python -m venv .venv
}

# Activate
$activateScript = ".venv\Scripts\Activate.ps1"
if (Test-Path $activateScript) {
    & $activateScript
} else {
    Write-Host "ERROR: Could not find .venv\Scripts\Activate.ps1" -ForegroundColor Red
    Write-Host "Make sure Python 3.11+ is installed and in PATH." -ForegroundColor Yellow
    exit 1
}

# ── 3. Install dependencies ──────────────────────────────────────────────────
Write-Host ""
Write-Host "Installing dependencies..." -ForegroundColor Cyan
pip install -e ".[dev]" --quiet

# ── 4. Run Alembic migrations ────────────────────────────────────────────────
Write-Host ""
Write-Host "Running database migrations..." -ForegroundColor Cyan
alembic upgrade head
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: Alembic migration failed!" -ForegroundColor Red
    exit 1
}
Write-Host "Migrations OK" -ForegroundColor Green

# ── 5. Start the server ──────────────────────────────────────────────────────
Write-Host ""
Write-Host "Starting TruckLink on http://localhost:$Port" -ForegroundColor Green
Write-Host "Press Ctrl+C to stop." -ForegroundColor Gray
Write-Host ""

if ($NoReload) {
    uvicorn app.main:app --host 0.0.0.0 --port $Port
} else {
    uvicorn app.main:app --host 0.0.0.0 --port $Port --reload
}
