# Start ComfyUI at http://127.0.0.1:8188
#   .\scripts\start.ps1 -Cpu    # no NVIDIA GPU
param([switch]$Cpu)
$Root = Split-Path -Parent $PSScriptRoot
$comfyArgs = @("$Root\ComfyUI\main.py", "--listen", "127.0.0.1", "--port", "8188")
if ($Cpu) { $comfyArgs += "--cpu" }
& "$Root\.venv\Scripts\python.exe" @comfyArgs
