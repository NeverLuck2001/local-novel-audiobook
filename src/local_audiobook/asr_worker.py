"""JSON-line ASR protocol. All library output goes to stderr, all inputs local."""
import contextlib
import json
import os
from pathlib import Path
import sys


def reply(value):
    print(json.dumps(value, ensure_ascii=False), flush=True)


def main():
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    for key in ["HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_HUB_DISABLE_TELEMETRY", "DO_NOT_TRACK"]:
        os.environ[key] = "1"
    os.environ["GRADIO_ANALYTICS_ENABLED"] = "False"
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    with contextlib.redirect_stdout(sys.stderr):
        import torch
        from qwen_asr import Qwen3ASRModel
        model = Qwen3ASRModel.from_pretrained(sys.argv[1], dtype=torch.bfloat16,
                                             device_map="cuda:0", attn_implementation="sdpa",
                                             max_inference_batch_size=1, max_new_tokens=1024,
                                             local_files_only=True)
    reply({"ready": True})
    for line in sys.stdin:
        try:
            request = json.loads(line)
            path = Path(request["audio"])
            if not path.is_file():
                raise ValueError("ASR only accepts existing local files")
            with contextlib.redirect_stdout(sys.stderr):
                result = model.transcribe(audio=str(path), language="Chinese", context="")
            reply({"text": result[0].text})
        except Exception as exc:
            reply({"error": f"{type(exc).__name__}: {exc}"})


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        reply({"error": f"{type(exc).__name__}: {exc}"})
        raise SystemExit(1) from exc
