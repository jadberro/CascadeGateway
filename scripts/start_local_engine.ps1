# ==============================================================================
# CascadeGateway: Windows Local Engine Launcher & Warmup for RTX 5090
# Supports: Ollama (Native Windows) & vLLM (Docker / WSL / Native)
# ==============================================================================
[CmdletBinding()]
param(
    [switch]$NoWarmup,
    [switch]$Silent,
    [string]$TargetModel = "qwen2.5-coder:32b"
)

$ErrorActionPreference = "SilentlyContinue"

function Write-EngineLog {
    param([string]$Message, [string]$Level = "INFO")
    if ($Silent) { return }
    $timestamp = Get-Date -Format "HH:mm:ss"
    switch ($Level) {
        "SUCCESS" { Write-Host "[$timestamp] [SUCCESS] $Message" -ForegroundColor Green }
        "WARN"    { Write-Host "[$timestamp] [WARN]    $Message" -ForegroundColor Yellow }
        "ERROR"   { Write-Host "[$timestamp] [ERROR]   $Message" -ForegroundColor Red }
        default   { Write-Host "[$timestamp] [INFO]    $Message" -ForegroundColor Cyan }
    }
}

Write-EngineLog "Checking local AI engines on RTX 5090 workstation..."

# 1. Probe vLLM / OpenAI-compatible endpoint (Port 8000)
$vllmRunning = $false
try {
    $vllmRes = Invoke-RestMethod -Uri "http://127.0.0.1:8000/v1/models" -Method Get -TimeoutSec 2
    if ($vllmRes -and $vllmRes.data) {
        $vllmRunning = $true
        Write-EngineLog "vLLM active on http://127.0.0.1:8000 with $(($vllmRes.data | Measure-Object).Count) models loaded." "SUCCESS"
    }
} catch {}

# 2. Probe Ollama endpoint (Port 11434)
$ollamaRunning = $false
try {
    $ollamaRes = Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/tags" -Method Get -TimeoutSec 2
    if ($ollamaRes -and $ollamaRes.models) {
        $ollamaRunning = $true
        Write-EngineLog "Ollama active on http://127.0.0.1:11434 with $(($ollamaRes.models | Measure-Object).Count) models." "SUCCESS"
    }
} catch {}

# 3. If neither running, start Ollama serve in background
if (-not $ollamaRunning -and -not $vllmRunning) {
    Write-EngineLog "Neither vLLM nor Ollama running. Starting Ollama in background..." "WARN"
    try {
        $psi = New-Object System.Diagnostics.ProcessStartInfo
        $psi.FileName = "ollama"
        $psi.Arguments = "serve"
        $psi.WindowStyle = [System.Diagnostics.ProcessWindowStyle]::Hidden
        $psi.CreateNoWindow = $true
        $psi.UseShellExecute = $false
        [System.Diagnostics.Process]::Start($psi) | Out-Null
        Start-Sleep -Seconds 3

        $ollamaRes = Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/tags" -Method Get -TimeoutSec 3
        if ($ollamaRes) {
            $ollamaRunning = $true
            Write-EngineLog "Ollama daemon successfully started in background." "SUCCESS"
        }
    } catch {
        Write-EngineLog "Could not start Ollama automatically: $_" "ERROR"
    }
}

# 4. Model Warmup in VRAM (Keep-Alive indefinitely: -1)
if (-not $NoWarmup) {
    if ($ollamaRunning) {
        Write-EngineLog "Warming up '$TargetModel' in RTX 5090 VRAM (keep_alive: -1)..."
        try {
            $warmupBody = @{
                model = $TargetModel
                prompt = "System initialization. Ready."
                keep_alive = -1
                stream = $false
            } | ConvertTo-Json

            $warmupRes = Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/generate" `
                -Method Post `
                -Body $warmupBody `
                -ContentType "application/json" `
                -TimeoutSec 60

            Write-EngineLog "Model '$TargetModel' resident in 32GB VRAM. Warmup complete!" "SUCCESS"
        } catch {
            Write-EngineLog "Warmup request failed: $_" "WARN"
        }
    } elseif ($vllmRunning) {
        Write-EngineLog "vLLM handles resident caching automatically via Prefix Caching." "SUCCESS"
    } else {
        Write-EngineLog "No local engine could be contacted." "ERROR"
        exit 1
    }
}

Write-EngineLog "Local engine check completed." "SUCCESS"
