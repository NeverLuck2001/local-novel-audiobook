param([switch]$DownloadModels)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $taskRoot
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    py -3.12 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 is required' }
}
$taskPython = Join-Path $taskRoot '.venv\Scripts\python.exe'
& $taskPython -m pip install torch==2.8.0 torchaudio==2.8.0 --index-url https://download.pytorch.org/whl/cu128
if ($LASTEXITCODE -ne 0) { throw 'PyTorch installation failed' }
& $taskPython -m pip install -e '.[tts,ui,dev]' modelscope
if ($LASTEXITCODE -ne 0) { throw 'Pipeline installation failed' }
& (Join-Path $PSScriptRoot 'setup-asr.ps1') -DownloadModels:$DownloadModels
if ($LASTEXITCODE -ne 0) { throw 'ASR setup failed' }
if ($DownloadModels) {
    & $taskPython scripts/download_models.py preset --provider modelscope
    if ($LASTEXITCODE -ne 0) { throw 'Model download failed; rerun to resume' }
}
& $taskPython audiobook.py --doctor
