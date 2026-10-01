"""Stable identities, offline settings, and atomic file replacement."""
import hashlib
import json
import os
from pathlib import Path
import re
from datetime import datetime, timezone


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    default=str, separators=(",", ":")).encode("utf-8")).hexdigest()


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def atomic_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2, default=str, allow_nan=False)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_name(name: str, limit: int = 60) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")[:limit]
    if re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", name):
        name = "_" + name
    return name or "book"


def offline():
    for key in ["HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_HUB_DISABLE_TELEMETRY",
                "DO_NOT_TRACK", "GRADIO_ANALYTICS_ENABLED"]:
        os.environ[key] = "False" if key == "GRADIO_ANALYTICS_ENABLED" else "1"
    os.environ["TOKENIZERS_PARALLELISM"] = "false"


def model_identity(path: Path) -> dict:
    if not path.is_dir() or not (path / "config.json").is_file():
        raise FileNotFoundError(f"Local model is missing: {path}. Run the download command first.")
    files = sorted(p for p in path.rglob("*") if p.is_file() and not any(
        part.startswith(".") for part in p.relative_to(path).parts)
        and p.suffix in {".json", ".safetensors", ".bin", ".txt", ".model", ".tiktoken", ".npz"}
        and p.name != "download-manifest.json")
    if not any(p.suffix in {".safetensors", ".bin"} for p in files):
        raise FileNotFoundError(f"No weights found in {path}")
    hashes = {p.relative_to(path).as_posix(): file_hash(p) for p in files}
    metadata = path / "download-manifest.json"
    provenance = json.loads(metadata.read_text(encoding="utf-8")) if metadata.exists() else {}
    return {"path": str(path), "sha256": digest(hashes), "files": hashes,
            "repository": provenance.get("repository"), "revision": provenance.get("revision")}
