# Set up ComfyUI + the creator node pack on Windows.
#   .\scripts\setup.ps1 -Cpu                     # laptop without an NVIDIA GPU
#   .\scripts\setup.ps1                          # NVIDIA GPU (CUDA)
#   .\scripts\setup.ps1 -Models z-image-turbo    # also download a model profile (see models.json)
param(
    [switch]$Cpu,
    [string]$Models = ""
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Find-Python {
    foreach ($v in "3.12", "3.13", "3.11") {
        try { $p = & py "-$v" -c "import sys; print(sys.executable)" 2>$null; if ($LASTEXITCODE -eq 0 -and $p) { return $p.Trim() } } catch {}
    }
    $uv = Get-ChildItem "$env:APPDATA\uv\python" -Directory -Filter "cpython-3.12*" -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($uv) { return (Join-Path $uv.FullName "python.exe") }
    throw "Python 3.11-3.13 not found. Install Python 3.12 from python.org and re-run."
}

if (-not (Test-Path "ComfyUI")) {
    git clone --depth 1 https://github.com/comfyanonymous/ComfyUI.git ComfyUI
}

if (-not (Test-Path ".venv")) {
    $py = Find-Python
    Write-Host "Creating venv with $py"
    & $py -m venv .venv
}
$venvPy = Join-Path $Root ".venv\Scripts\python.exe"
& $venvPy -m pip install --upgrade pip

$torchIndex = if ($Cpu) { "https://download.pytorch.org/whl/cpu" } else { "https://download.pytorch.org/whl/cu128" }
& $venvPy -m pip install torch torchvision torchaudio --index-url $torchIndex
& $venvPy -m pip install -r ComfyUI\requirements.txt

$link = "ComfyUI\custom_nodes\creator_nodes"
if (-not (Test-Path $link)) {
    New-Item -ItemType Junction -Path $link -Target (Join-Path $Root "creator_nodes") | Out-Null
    Write-Host "Linked creator_nodes into ComfyUI\custom_nodes"
}
New-Item -ItemType Directory -Force "ComfyUI\input\batch" | Out-Null

if ($Models) { & $venvPy tools\download_models.py $Models }

Write-Host ""
Write-Host "Done. Start ComfyUI with: .\scripts\start.ps1$(if ($Cpu) { ' -Cpu' })"
