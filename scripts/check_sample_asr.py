"""Run real local ASR verification on the generated original Chinese fixture."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from local_audiobook.asr import ASRWorker
from local_audiobook.config import load_config
from local_audiobook.quality import compare_text
from local_audiobook.util import atomic_json, offline


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    root = Path(__file__).resolve().parents[1]
    offline()
    cfg = load_config(root / "config.yaml")
    worker = ASRWorker(cfg.quality, root / "work/asr-validation.log")
    manifest_path = next((root / "work").glob("sample-*/manifest.json"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    report = []
    try:
        for chapter in manifest["chapters"]:
            for segment in chapter["segments"]:
                audio = Path(segment["output_path"])
                if audio.is_file():
                    text = worker.transcribe(audio)
                    result = {"chapter": chapter["index"], "segment": segment["index"],
                              "source": segment["text"], **compare_text(segment["text"], text)}
                    print(json.dumps(result, ensure_ascii=False), flush=True)
                    report.append(result)
    finally:
        worker.close()
    atomic_json(root / "docs/sample-asr.json", report)


if __name__ == "__main__":
    main()
