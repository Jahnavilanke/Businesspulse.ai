<#
.SYNOPSIS
  BusinessPulse AI task runner for Windows (PowerShell). Linux/macOS: see Makefile.
.EXAMPLE
  ./scripts/bp.ps1 setup      # create .venv and install requirements
  ./scripts/bp.ps1 pipeline   # full run: download -> quality -> dbt -> ML -> alerts -> exports
  ./scripts/bp.ps1 app        # start the Streamlit app on http://localhost:8501
  ./scripts/bp.ps1 test       # unit + integrity + UI smoke tests
  ./scripts/bp.ps1 docs       # dbt docs + data dictionary
  ./scripts/bp.ps1 reset      # delete derived data (keeps the downloaded dataset)
#>
param([Parameter(Position = 0)][string]$Task = "help")
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$Py = Join-Path $Root ".venv\Scripts\python.exe"

switch ($Task) {
  "setup" {
    if (-not (Test-Path $Py)) { python -m venv .venv }
    & $Py -m pip install -r requirements-dev.txt
    if (-not (Test-Path ".env")) { Copy-Item .env.example .env }
  }
  "pipeline" { & $Py -m businesspulse.pipeline.runner }
  "pipeline-fast" { & $Py -m businesspulse.pipeline.runner --skip-ml }
  "app" { & $Py -m streamlit run app.py }
  "test" { & $Py -m pytest tests -q }
  "docs" {
    $env:BP_WAREHOUSE_PATH = Join-Path $Root "data\warehouse\businesspulse.duckdb"
    $env:BP_LAKE_DIR = (Join-Path $Root "data\lake") -replace "\\", "/"
    & (Join-Path $Root ".venv\Scripts\dbt.exe") docs generate --project-dir dbt --profiles-dir dbt
    & $Py -m businesspulse.catalog
  }
  "reset" {
    foreach ($d in "data\lake", "data\warehouse", "data\state", "data\exports", "data\mlruns", "data\lineage", "dbt\target") {
      if (Test-Path $d) { Remove-Item -Recurse -Force $d }
    }
  }
  default { Get-Help $PSCommandPath -Detailed }
}
