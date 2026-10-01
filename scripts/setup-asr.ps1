param([switch]$DownloadModels)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $taskRoot
if (-not (Test-Path -LiteralPath '.venv-asr\Scripts\python.exe')) {
    py -3.12 -m venv .venv-asr
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 is required' }
}
$taskPython = Join-Path $taskRoot '.venv-asr\Scripts\python.exe'
& $taskPython -m pip install torch==2.8.0 torchaudio==2.8.0 --index-url https://download.pytorch.org/whl/cu128
if ($LASTEXITCODE -ne 0) { throw 'ASR PyTorch installation failed' }
& $taskPython -m pip install qwen-asr==0.0.6 rapidfuzz==3.14.6
if ($LASTEXITCODE -ne 0) { throw 'ASR installation failed' }
if ($DownloadModels) {
    & (Join-Path $taskRoot '.venv\Scripts\python.exe') scripts/download_models.py asr --provider modelscope
    if ($LASTEXITCODE -ne 0) { throw 'ASR model download failed' }
}
