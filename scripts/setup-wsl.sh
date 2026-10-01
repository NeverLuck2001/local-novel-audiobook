#!/usr/bin/env bash
# Optional official Linux environment; run from a WSL Ubuntu shell.
set -euo pipefail
cd "$(dirname "$0")/.."
python3.12 -m venv .venv-linux
.venv-linux/bin/python -m pip install torch==2.8.0 torchaudio==2.8.0 --index-url https://download.pytorch.org/whl/cu128
.venv-linux/bin/python -m pip install -e '.[tts,ui,dev]' modelscope
python3.12 -m venv .venv-asr-linux
.venv-asr-linux/bin/python -m pip install torch==2.8.0 torchaudio==2.8.0 --index-url https://download.pytorch.org/whl/cu128
.venv-asr-linux/bin/python -m pip install qwen-asr==0.0.6 rapidfuzz==3.14.6
printf '%s\n' 'Set quality.asr_python to .venv-asr-linux/bin/python in a config beside config.yaml.'
