"""Download only official repositories and record the exact local weight hashes."""
import argparse
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from local_audiobook.util import atomic_json, model_identity, now

MODELS = {
    "preset": "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice",
    "clone": "Qwen/Qwen3-TTS-12Hz-1.7B-Base",
    "design": "Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign",
    "asr": "Qwen/Qwen3-ASR-0.6B",
    "asr-large": "Qwen/Qwen3-ASR-1.7B",
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("models", nargs="+", choices=MODELS)
    parser.add_argument("--provider", choices=["huggingface", "modelscope"], default="huggingface")
    parser.add_argument("--revision", default=None)
    args = parser.parse_args()
    os.environ.pop("HF_HUB_OFFLINE", None)
    os.environ.pop("TRANSFORMERS_OFFLINE", None)
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    root = Path(__file__).resolve().parents[1] / "models"
    for key in args.models:
        repo = MODELS[key]
        destination = root / repo.split("/")[1]
        if args.provider == "huggingface":
            from huggingface_hub import HfApi, snapshot_download
            info = HfApi().model_info(repo, revision=args.revision)
            revision = info.sha
            snapshot_download(repo, revision=revision, local_dir=str(destination), max_workers=4,
                              ignore_patterns=["*.md", "*.png", "*.jpg", ".gitattributes"])
        else:
            from modelscope import snapshot_download
            revision = args.revision or "master"
            snapshot_download(repo, revision=revision, local_dir=str(destination))
        identity = model_identity(destination)
        atomic_json(destination / "download-manifest.json", {
            "repository": repo, "provider": args.provider, "revision": revision,
            "downloaded_at": now(), "content_sha256": identity["sha256"], "files": identity["files"]})
        print(f"Downloaded {repo} to {destination}; content={identity['sha256']}", flush=True)


if __name__ == "__main__":
    main()
