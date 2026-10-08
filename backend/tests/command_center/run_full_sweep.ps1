# Full model sweep — one-command runner (on-demand, NOT CI).
#
# Tomorrow's "go": from backend/ run
#     .\tests\command_center\run_full_sweep.ps1
#
# What it does:
#   - sets BEDROCK_ENABLED=true + the offline HF/Transformers guards + a temp DATA_DIR
#   - WIPES the on-disk model cache so EVERY model makes fresh live calls, giving
#     a uniform dataset with real latency for all 8 models (the prior run reused a
#     warm cache for two models, so their latency read 0)
#   - runs the sweep across all approved models, redirecting the verbose per-model
#     output to _sweep_run.log (so it does not flood an agent session)
#   - prints the final compact scorecard + results path at the end
#
# NOTE: the sweep exercises the correction AGENT's TEXT path (mc.complete +
# extract_json), not the production tool-use tiers. The recent tool-name
# sanitizer fix affects the production interpreter/semantic path, NOT these
# numbers — see docs/testing/model-sweep-results.md "Staging / runbook".
#
# Requires: Bedrock reachable with credentials in the environment (the air-gapped
# enclave has Bedrock). Spends live Bedrock tokens on the cold cache.

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path       # ...\backend\tests\command_center
$backend = (Resolve-Path (Join-Path $here "..\..")).Path       # ...\backend
$py = Join-Path $backend "..\.venv\Scripts\python.exe"
$log = Join-Path $here "_sweep_run.log"
$cache = Join-Path $here "_model_cache"

$env:BEDROCK_ENABLED = "true"
$env:HF_HUB_OFFLINE = "1"
$env:TRANSFORMERS_OFFLINE = "1"
$env:DATA_DIR = Join-Path $env:TEMP "fullsweep_fresh"

Write-Host "[run_full_sweep] wiping model cache for a fresh cold run: $cache"
if (Test-Path $cache) { Remove-Item -Recurse -Force $cache }

Write-Host "[run_full_sweep] starting sweep (verbose -> $log). This spends live Bedrock tokens."
Push-Location $backend
try {
    & $py -m tests.command_center.model_sweep *> $log
} finally {
    Pop-Location
}

Write-Host "[run_full_sweep] done. Scorecard tail:"
Get-Content $log -Tail 20
Write-Host "[run_full_sweep] results JSON: $(Join-Path $here 'model_sweep_results.json')"
