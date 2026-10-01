"""Real inference with DNS and socket connections disabled in the current process."""
import argparse
import json
import os
from pathlib import Path
import socket
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def forbidden(*args, **kwargs):
    raise RuntimeError("Network is disabled for this validation run")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--asr", action="store_true")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    socket.getaddrinfo = forbidden
    socket.create_connection = forbidden
    socket.socket.connect = forbidden
    socket.socket.connect_ex = forbidden
    socket.socket.sendto = forbidden
    for key in ["HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_HUB_DISABLE_TELEMETRY", "DO_NOT_TRACK"]:
        os.environ[key] = "1"
    os.environ["GRADIO_ANALYTICS_ENABLED"] = "False"
    text = "雨已经停了。她打开窗户，安静地听着风吹过树叶的声音。"
    audio = ROOT / "output/offline-verification.wav"
    if args.asr:
        import torch
        from qwen_asr import Qwen3ASRModel
        model = Qwen3ASRModel.from_pretrained(str(ROOT / "models/Qwen3-ASR-0.6B"), device_map="cuda:0",
                                            dtype=torch.bfloat16, attn_implementation="sdpa", local_files_only=True)
        transcription = model.transcribe(audio=str(audio), language="Chinese", context="")[0].text
        from local_audiobook.quality import compare_text
        from local_audiobook.util import atomic_json
        result = {"network_blocked": True, "model": "Qwen3-ASR-0.6B", **compare_text(text, transcription)}
        atomic_json(ROOT / "docs/offline-asr.json", result)
    else:
        from local_audiobook.config import load_config
        from local_audiobook.engine import QwenEngine
        from local_audiobook.util import atomic_json
        engine = QwenEngine(load_config(ROOT / "config.yaml"))
        try:
            metrics = engine.generate(text, audio, 20261001)
            result = {"network_blocked": True, "model": "Qwen3-TTS-12Hz-1.7B-CustomVoice", **metrics}
            atomic_json(ROOT / "docs/offline-tts.json", result)
        finally:
            engine.unload()
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
