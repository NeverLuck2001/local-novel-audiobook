"""Persistent local UI queue and read-only monitoring of conversion state."""
from collections import defaultdict
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import uuid

import psutil
from filelock import FileLock
import yaml

from .config import Config
from . import __version__
from .audio import AudioTools
from .asr import ASRWorker
from .engine import QwenEngine
from .pipeline import Pipeline
from .state import State
from .util import atomic_json, digest, file_hash, now, safe_name
from .voices import MAX_REFERENCE_CLIPS, prepare_references

LOG = logging.getLogger(__name__)
BOOK_SUFFIXES = {".txt", ".epub"}
REFERENCE_SUFFIXES = {".wav", ".flac", ".mp3", ".m4a", ".ogg"}
TERMINAL = {"completed", "failed", "paused", "cancelled"}
ACTIVE = {"running", "pausing", "cancelling"}
VOICES = [
    {"id": "Serena", "label": "Serena · 温柔女声", "language": "Chinese"},
    {"id": "Vivian", "label": "Vivian · 明亮女声", "language": "Chinese"},
    {"id": "Uncle_Fu", "label": "Uncle Fu · 沉稳男声", "language": "Chinese"},
    {"id": "Dylan", "label": "Dylan · 北京男声", "language": "Chinese"},
    {"id": "Eric", "label": "Eric · 四川男声", "language": "Chinese"},
    {"id": "Ryan", "label": "Ryan · 英语男声", "language": "English"},
    {"id": "Aiden", "label": "Aiden · 美式男声", "language": "English"},
    {"id": "Ono_Anna", "label": "Ono Anna · 日语女声", "language": "Japanese"},
    {"id": "Sohee", "label": "Sohee · 韩语女声", "language": "Korean"},
]


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def inside(path: Path, root: Path) -> bool:
    return path.resolve().is_relative_to(root.resolve())


def tail(path: Path, lines: int = 60) -> list[str]:
    try:
        with path.open("rb") as stream:
            stream.seek(max(0, path.stat().st_size - 24000))
            return stream.read().decode("utf-8", errors="replace").splitlines()[-lines:]
    except OSError:
        return []


def state_summary(work: Path) -> dict:
    """Read completed results without opening SQLite for writing."""
    progress = read_json(work / "progress.json", {})
    manifest = read_json(work / "manifest.json", {}) or read_json(work / "plan.json", {})
    occurrences = defaultdict(int)
    for chapter in manifest.get("chapters", []):
        for segment in chapter.get("segments", []):
            if "id" in segment:
                occurrences[segment["id"]] += 1
    counts, chapters, qc = defaultdict(int), {}, {"checked": 0, "passed": 0, "failed": 0,
                                               "retries": 0, "mean_cer": None}
    audio_seconds = synthesized_audio_seconds = generation_seconds = chars = 0.0
    cer_values = []
    database = work / "state.sqlite3"
    if database.is_file():
        try:
            with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True, timeout=1) as db:
                for (raw,) in db.execute("SELECT data FROM segments"):
                    record = json.loads(raw)
                    if occurrences and record.get("id") not in occurrences:
                        continue
                    multiplier = occurrences.get(record.get("id"), 1)
                    status = record.get("status", "pending")
                    counts[status] += multiplier
                    quality = record.get("qc")
                    if quality:
                        qc["checked"] += 1
                        qc["passed" if quality.get("pass") else "failed"] += 1
                        if quality.get("asr", {}).get("cer") is not None:
                            cer_values.append(float(quality["asr"]["cer"]))
                    qc["retries"] += int(record.get("retry_count", 0))
                    if status == "completed":
                        audio_seconds += float(record.get("duration", 0)) * multiplier
                        synthesized_audio_seconds += float(record.get("duration", 0))
                        generation_seconds += float(record.get("generation_seconds", 0))
                        chars += len(record.get("spoken_text", record.get("text", ""))) * multiplier
        except (sqlite3.Error, ValueError, TypeError, OSError):
            LOG.debug("Conversion state is temporarily unavailable: %s", database, exc_info=True)
    if cer_values:
        qc["mean_cer"] = sum(cer_values) / len(cer_values)
    all_chapters = manifest.get("chapters", [])
    total = progress.get("total", sum(len(c.get("segments", [])) for c in all_chapters))
    for chapter in all_chapters:
        index = chapter["index"]
        chapters[index] = {"index": index, "title": chapter["title"],
                           "segments": len(chapter.get("segments", [])),
                           "status": chapter.get("status", "pending"),
                           "duration": chapter.get("duration")}
    completed = counts["completed"]
    return {"title": manifest.get("title", work.name), "book_id": manifest.get("book_id", work.name),
            "source": manifest.get("source"), "status": progress.get("status", manifest.get("status", "pending")),
            "complete_book": manifest.get("complete_book", True),
            "export_scope": manifest.get("export_scope", "complete_book"),
            "selected_chapters": manifest.get("selected_chapters", [c["index"] for c in all_chapters]),
            "total_chapters": manifest.get("total_chapters", len(all_chapters)),
            "phase": progress.get("phase"), "elapsed_seconds": progress.get("elapsed_seconds"),
            "eta_seconds": progress.get("eta_seconds"), "eta_estimated": progress.get("eta_estimated", False),
            "active_batch_size": progress.get("active_batch_size"),
            "run_audio_seconds": progress.get("audio_seconds"),
            "run_generation_seconds": progress.get("generation_seconds"),
            "run_rtf": progress.get("rtf"), "speed": progress.get("audio_seconds_per_second"),
            "run_started_at": progress.get("started_at"),
            "segments_per_minute": progress.get("segments_per_minute"),
            "remaining_chars": progress.get("remaining_chars"),
            "completed": completed, "total": total, "failed": counts["failed"],
            "staged": counts["generated"], "percent": round(completed / total * 100, 1) if total else 0,
            "audio_seconds": audio_seconds, "synthesized_audio_seconds": synthesized_audio_seconds,
            "generation_seconds": generation_seconds,
            "rtf": generation_seconds / synthesized_audio_seconds if synthesized_audio_seconds else None,
            "rtf_source": "historical" if synthesized_audio_seconds else "unavailable",
            "chars": int(chars), "chapters": list(chapters.values()), "qc": qc,
            "exports": manifest.get("exports", []), "updated_at": progress.get("updated_at"),
            "error": manifest.get("error"), "manifest": manifest}


class JobManager:
    """Run one owned conversion at a time, without disturbing external jobs."""

    def __init__(self, app_root: Path, workspace: Path | None = None):
        self.app_root = app_root.resolve()
        self.workspace = (workspace or app_root).resolve()
        self.root = self.app_root / "work" / "ui"
        self.output_root = self.app_root / "output" / "ui"
        self.root.mkdir(parents=True, exist_ok=True)
        self.output_root.mkdir(parents=True, exist_ok=True)
        self.files_path = self.root / "files.json"
        self.files = read_json(self.files_path, {})
        self.voices_path = self.root / "voices.json"
        self.voice_profiles = read_json(self.voices_path, {})
        self.monitor_records_path = self.root / "monitor-records.json"
        self.removed_monitors = read_json(self.monitor_records_path, {})
        self.jobs = {}
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.thread = None
        self.queue_lock = FileLock(str(self.root / "queue.lock"))
        self.children = {}
        self.log_handles = {}
        self.summary_cache = {}
        self.process_cache = (0.0, [])
        self.hardware_cache = (0.0, None)
        for path in (self.root / "jobs").glob("*/job.json"):
            job = read_json(path)
            if job and job.get("id") == path.parent.name:
                self.jobs[job["id"]] = job
        self.default_config = self._defaults()
        self.audio = AudioTools(self.default_config)

    def _defaults(self) -> Config:
        data = yaml.safe_load((self.app_root / "config.yaml").read_text(encoding="utf-8")) or {}
        config = Config.model_validate(data)
        config.tts.model_path = (self.workspace / config.tts.model_path).resolve()
        config.quality.asr_python = (self.workspace / config.quality.asr_python).resolve()
        config.quality.asr_model_path = (self.workspace / config.quality.asr_model_path).resolve()
        if config.voice.reference_audio:
            config.voice.reference_audio = (self.workspace / config.voice.reference_audio).resolve()
        config.work_root = self.root
        config.output.root = self.output_root
        return config

    def model_descriptors(self) -> list[dict]:
        models = []
        for path in sorted((self.workspace / "models").glob("Qwen3-TTS*")):
            if not path.is_dir():
                continue
            mode = "clone" if "Base" in path.name else "design" if "VoiceDesign" in path.name else "preset"
            models.append({"id": path.name, "label": path.name, "path": str(path.resolve()),
                           "mode": mode, "available": (path / "config.json").is_file()})
        if not models:
            path = self.default_config.tts.model_path
            models.append({"id": path.name, "label": path.name, "path": str(path),
                           "mode": "preset", "available": False})
        return models

    def info(self) -> dict:
        defaults = self.default_config.model_dump(mode="json")
        defaults.pop("work_root", None)
        defaults["output"].pop("root", None)
        try:
            pitch_available = self.audio.supports_rubberband()
        except (OSError, RuntimeError, subprocess.TimeoutExpired):
            pitch_available = False
        return {"voices": VOICES, "defaults": defaults, "default_voice_revision": "mature-v1",
                "models": self.model_descriptors(),
                "workspace": str(self.workspace), "version": __version__, "local_only": True,
                "hardware": self.hardware(),
                "features": {"native_picker": True, "preview": True, "pause": True,
                             "cancel": True, "queue_ordering": True, "archive": True,
                             "monitor_archive": True,
                              "reference_preparation": True, "voice_library": True,
                              "reference_asr": (self.default_config.quality.asr_python.is_file()
                                                and (self.default_config.quality.asr_model_path / "config.json").is_file()),
                             "chapter_selection": True, "pitch_adjustment": pitch_available,
                             "voice_clone": any(
                                 m["mode"] == "clone" and m["available"] for m in self.model_descriptors())}}

    def hardware(self) -> dict:
        stamp, cached = self.hardware_cache
        if cached is not None and time.monotonic() - stamp < 10:
            return cached
        result = {"device": self.default_config.tts.device, "gpu": None, "vram_gb": None,
                  "vram_used_gb": None, "driver": None}
        executable = shutil.which("nvidia-smi")
        if executable:
            try:
                report = subprocess.run([executable, "--query-gpu=name,memory.total,memory.used,driver_version",
                                         "--format=csv,noheader,nounits"], capture_output=True, text=True,
                                        encoding="utf-8", errors="replace", timeout=5,
                                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                lines = report.stdout.strip().splitlines()
                index = int(self.default_config.tts.device.split(":")[-1]) if ":" in self.default_config.tts.device else 0
                if report.returncode == 0 and lines and index < len(lines):
                    name, total, used, driver = [value.strip() for value in lines[index].split(",")]
                    result.update(gpu=name, vram_gb=round(float(total) / 1024, 1),
                                  vram_used_gb=round(float(used) / 1024, 1), driver=driver)
            except (OSError, ValueError, subprocess.TimeoutExpired):
                LOG.debug("GPU status is unavailable", exc_info=True)
        self.hardware_cache = (time.monotonic(), result)
        return result

    def _save(self, job: dict):
        job["updated_at"] = now()
        atomic_json(self.root / "jobs" / job["id"] / "job.json", job)

    def register_file(self, path: Path, *, reference=False, uploaded=False) -> dict:
        path = path.resolve()
        allowed = REFERENCE_SUFFIXES if reference else BOOK_SUFFIXES
        if not path.is_file() or path.suffix.lower() not in allowed:
            raise ValueError("Unsupported input file")
        identifier = uuid.uuid4().hex
        item = {"id": identifier, "name": path.name, "path": str(path), "size": path.stat().st_size,
                "kind": "reference" if reference else "book", "uploaded": uploaded, "created_at": now()}
        with self.lock:
            self.files[identifier] = item
            atomic_json(self.files_path, self.files)
        return item

    def register_upload(self, path: Path, name: str, *, reference=False) -> dict:
        item = self.register_file(path, reference=reference, uploaded=True)
        with self.lock:
            item["name"] = name
            atomic_json(self.files_path, self.files)
        return item

    def list_files(self) -> list[dict]:
        with self.lock:
            return [dict(item) for item in self.files.values() if Path(item["path"]).is_file()]

    def reference_file(self, identifier: str) -> dict:
        with self.lock:
            item = self.files.get(identifier)
            if not item or item["kind"] != "reference" or not Path(item["path"]).is_file():
                raise KeyError(identifier)
            return dict(item)

    def list_voices(self) -> list[dict]:
        with self.lock:
            return [dict(profile) for profile in self.voice_profiles.values()
                    if not profile.get("archived") and Path(profile["reference"]["path"]).is_file()]

    def save_voice(self, name: str, reference_id: str, text: str) -> dict:
        name, text = name.strip(), text.strip()
        if not 1 <= len(name) <= 80 or not 1 <= len(text) <= 12000:
            raise ValueError("Enter a voice name of 1 to 80 characters and an exact reference transcript")
        with self.lock:
            reference = self.reference_file(reference_id)
            if not reference.get("prepared"):
                raise ValueError("Prepare the recordings before saving a reusable voice")
            identifier = uuid.uuid4().hex[:16]
            directory = self.root / "voices" / identifier
            directory.mkdir(parents=True)
            target = directory / "reference.wav"
            shutil.copyfile(reference["path"], target)
            saved = self.register_file(target, reference=True)
            saved.update(prepared=True, duration=reference["duration"], sha256=file_hash(target))
            atomic_json(self.files_path, self.files)
            profile = {"id": identifier, "name": name, "reference": dict(saved), "text": text,
                       "duration": reference["duration"], "created_at": now(), "archived": False}
            for previous in self.voice_profiles.values():
                if previous["name"].casefold() == name.casefold() and not previous.get("archived"):
                    previous["archived"] = True
            self.voice_profiles[identifier] = profile
            atomic_json(self.voices_path, self.voice_profiles)
            return dict(profile)

    def archive_voice(self, identifier: str) -> dict:
        with self.lock:
            if identifier not in self.voice_profiles:
                raise KeyError(identifier)
            self.voice_profiles[identifier]["archived"] = True
            atomic_json(self.voices_path, self.voice_profiles)
            return {"id": identifier, "archived": True}

    def create_reference(self, clips: list[dict]) -> dict:
        """Use the existing persistent queue to serialize reference ASR with TTS."""
        if not 1 <= len(clips) <= MAX_REFERENCE_CLIPS:
            raise ValueError("Select between 1 and 5 recordings of the same speaker")
        records = [self.reference_file(clip["id"]) for clip in clips]
        needs_asr = any(not str(clip.get("text") or "").strip() for clip in clips)
        config = self.default_config.model_copy(deep=True)
        if needs_asr and (not config.quality.asr_python.is_file()
                          or not (config.quality.asr_model_path / "config.json").is_file()):
            raise ValueError("Enter the exact transcript or install the local ASR environment")
        identifier = uuid.uuid4().hex[:16]
        directory = self.root / "jobs" / identifier
        inputs = directory / "inputs"
        inputs.mkdir(parents=True)
        snapshots = []
        for index, (clip, item) in enumerate(zip(clips, records, strict=True), 1):
            target = inputs / f"reference-{index:02d}{Path(item['path']).suffix.lower()}"
            shutil.copyfile(item["path"], target)
            snapshots.append({**clip, "path": str(target), "name": item["name"]})
        atomic_json(directory / "reference-inputs.json", snapshots)
        config_path = directory / "config.yaml"
        config_path.write_text(yaml.safe_dump(config.model_dump(mode="json"), allow_unicode=True,
                                            sort_keys=False), encoding="utf-8")
        job = {"id": identifier, "kind": "reference", "title": "Voice reference preparation", "status": "queued",
               "sources": [], "config_path": str(config_path), "input_root": str(inputs),
               "work_root": str(directory / "reference"), "output_root": str(self.output_root / identifier),
               "stop_file": str(directory / "pause.request"), "log_path": str(directory / "conversion.log"),
               "chapters": None, "created_at": now(), "updated_at": now(), "pid": None,
               "planned_total": len(clips), "planned_chars": 0, "process_created": None,
               "attempt": 0, "retry_failed": False, "blocked_reason": None}
        with self.lock:
            self._enqueue(job)
            self.jobs[identifier] = job
            self._save(job)
        return self.detail(identifier)

    def input_files(self, identifiers: list[str]) -> list[dict]:
        if not identifiers or len(identifiers) > 100:
            raise ValueError("Select between 1 and 100 books")
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Duplicate selected files")
        records = []
        with self.lock:
            for identifier in identifiers:
                item = self.files.get(identifier)
                if not item or item["kind"] != "book" or not Path(item["path"]).is_file():
                    raise ValueError("Selected input is missing; select the file again")
                records.append(dict(item))
        return records

    def settings_config(self, settings: dict | None = None) -> tuple[Config, str | None]:
        settings = dict(settings or {})
        selection = settings.pop("chapters", None)
        selected_list = settings.pop("include_chapters", None)
        if selected_list is not None:
            if not isinstance(selected_list, list) or any(type(n) is not int or n < 1 for n in selected_list):
                raise ValueError("include_chapters must be a list of positive chapter numbers")
            selection = ",".join(str(n) for n in sorted(set(selected_list)))
        if selection is not None and not re.fullmatch(r"\d+(?:-\d+)?(?:,\d+(?:-\d+)?)*", str(selection)):
            raise ValueError("chapters must use the form 1,3-5")
        data = self.default_config.model_dump(mode="json")
        allowed = {"tts", "voice", "segment", "text", "quality", "output"}
        if set(settings) - allowed:
            raise ValueError("Unsupported settings group")
        for group, values in settings.items():
            if not isinstance(values, dict):
                raise ValueError("Each settings group must be an object")
            forbidden = ({"root"} if group == "output" else
                         {"asr_python", "asr_model_path"} if group == "quality" else set())
            if forbidden & set(values):
                raise ValueError("Runtime paths cannot be changed through the browser")
            data[group].update(values)
        model = Path(data["tts"]["model_path"])
        if not model.is_absolute():
            model = self.workspace / "models" / model.name
        registered_models = {m["path"] for m in self.model_descriptors()}
        if str(model.resolve()) not in registered_models:
            raise ValueError("Choose a local model listed in the interface")
        data["tts"]["model_path"] = str(model.resolve())
        reference = data["voice"].get("reference_audio")
        if reference:
            with self.lock:
                item = self.files.get(str(reference))
                if item and item["kind"] == "reference":
                    reference = item["path"]
                elif not any(f["kind"] == "reference" and f["path"] == str(reference)
                             for f in self.files.values()):
                    raise ValueError("Upload or select the reference audio first")
            data["voice"]["reference_audio"] = reference
        config = Config.model_validate(data)
        if config.voice.mode == "preset" and config.voice.speaker not in {v["id"] for v in VOICES}:
            raise ValueError("Unknown preset speaker")
        model_mode = next(m["mode"] for m in self.model_descriptors() if m["path"] == str(model.resolve()))
        if model_mode != config.voice.mode:
            raise ValueError("Voice mode does not match the selected local model")
        return config, str(selection) if selection else None

    def plan(self, identifiers: list[str], settings: dict | None) -> dict:
        config, selection = self.settings_config(settings)
        AudioTools(config).validate_delivery()
        selected = self._chapter_numbers(selection)
        return {"books": [self._plan_source(config, item, selected) for item in self.input_files(identifiers)]}

    @staticmethod
    def _audit_preview(audit: list[dict]) -> list[dict]:
        def bounded(value):
            if isinstance(value, str):
                return value[:500]
            if isinstance(value, list):
                return [bounded(item) for item in value[:20]]
            if isinstance(value, dict):
                return {key: bounded(item) for key, item in value.items()}
            return value
        return [bounded(item) for item in audit[:100]]

    def _plan_source(self, config: Config, item: dict, selected: set[int] | None) -> dict:
        # Planning shares conversion's segmentation, pronunciation replacements,
        # chapter validation, and audit. This method does not load a model.
        book, _, _, _, chapters = Pipeline(config).plan(Path(item["path"]), selected)
        return {"id": item.get("id"), "title": book.title, "author": book.author,
                "chapters": len(chapters), "all_chapters": len(book.chapters),
                "chapter_titles": [{"index": index, "title": chapter.title}
                                   for index, chapter in enumerate(book.chapters, 1)],
                "segments": sum(len(ch["segments"]) for ch in chapters),
                "chars": sum(len(ch["text"]) for ch in chapters),
                "spoken_chars": sum(len(s.get("spoken_text", s["text"])) for c in chapters for s in c["segments"]),
                "audit": self._audit_preview(book.audit), "audit_count": len(book.audit),
                "audit_truncated": len(book.audit) > 100,
                "preview": "\n\n".join(ch["text"] for ch in chapters)[:4000],
                "spoken_preview": "\n\n".join("".join(s.get("spoken_text", s["text"]) for s in c["segments"])
                                                for c in chapters)[:4000],
                "warnings": ["Text cleanup changes are recorded in the audit preview"] if book.audit else []}

    @staticmethod
    def _chapter_numbers(selection: str | None) -> set[int] | None:
        if not selection:
            return None
        selected = set()
        for part in selection.split(","):
            bounds = part.split("-")
            low, high = int(bounds[0]), int(bounds[-1])
            if low < 1 or high < low or high > 100000:
                raise ValueError("Invalid chapter range")
            selected.update(range(low, high + 1))
        return selected

    def create(self, identifiers: list[str], settings: dict | None, kind="convert", preview_text=None) -> dict:
        if kind not in {"convert", "preview"}:
            raise ValueError("Unknown job kind")
        config, selection = self.settings_config(settings)
        AudioTools(config).validate_delivery()
        if not (config.tts.model_path / "config.json").is_file():
            raise ValueError("The selected TTS model has not been downloaded")
        if config.quality.asr_check and (not config.quality.asr_python.is_file()
                                       or not (config.quality.asr_model_path / "config.json").is_file()):
            raise ValueError("Local ASR environment or model is missing")
        records = self.input_files(identifiers) if kind == "convert" else []
        if kind == "preview" and (not preview_text or len(preview_text.strip()) < 2 or len(preview_text) > 1500):
            raise ValueError("Preview text must contain between 2 and 1500 characters")
        identifier = uuid.uuid4().hex[:16]
        directory = self.root / "jobs" / identifier
        inputs = directory / "inputs"
        inputs.mkdir(parents=True)
        sources = []
        if kind == "preview":
            path = inputs / "voice-preview.txt"
            path.write_text(preview_text.strip(), encoding="utf-8")
            sources.append({"name": path.name, "path": str(path)})
            selection = None
        else:
            for index, item in enumerate(records, 1):
                target = inputs / f"{index:03d}-{safe_name(Path(item['name']).stem, 80)}{Path(item['name']).suffix.lower()}"
                shutil.copyfile(item["path"], target)
                sources.append({"id": item["id"], "name": item["name"], "path": str(target)})
        for source in sources:
            source["sha256"] = file_hash(Path(source["path"]))
        planned = [self._plan_source(config, source, self._chapter_numbers(selection)) for source in sources]
        config.work_root = directory / "runtime"
        config.output.root = self.output_root / identifier
        if config.voice.reference_audio:
            reference = directory / ("reference" + config.voice.reference_audio.suffix.lower())
            shutil.copyfile(config.voice.reference_audio, reference)
            config.voice.reference_audio = reference
        config_path = directory / "config.yaml"
        config_path.write_text(yaml.safe_dump(config.model_dump(mode="json"), allow_unicode=True, sort_keys=False),
                               encoding="utf-8")
        job = {"id": identifier, "kind": kind, "title": "Voice preview" if kind == "preview" else
               records[0]["name"] if len(records) == 1 else f"{len(records)} books", "status": "queued",
               "sources": sources, "config_path": str(config_path), "input_root": str(inputs),
               "work_root": str(config.work_root), "output_root": str(config.output.root),
               "stop_file": str(directory / "pause.request"), "log_path": str(directory / "conversion.log"),
               "chapters": selection, "created_at": now(), "updated_at": now(), "pid": None,
               "planned_total": sum(b["segments"] for b in planned),
               "planned_chars": sum(b["spoken_chars"] for b in planned),
               "process_created": None, "attempt": 0, "retry_failed": False, "blocked_reason": None}
        with self.lock:
            self._enqueue(job)
            self.jobs[identifier] = job
            self._save(job)
        return self.detail(identifier)

    def start(self):
        if self.thread is None:
            self.queue_lock.acquire(timeout=0)
            self.thread = threading.Thread(target=self._run, name="audiobook-ui-queue", daemon=True)
            self.thread.start()

    def close(self):
        # A running conversion is an independent child. Preserve it and its PID
        # so reopening the interface can reattach without interrupting audio.
        self.stop.set()
        if self.thread:
            self.thread.join()
        for handle in self.log_handles.values():
            handle.close()
        self.log_handles.clear()
        if self.queue_lock.is_locked:
            self.queue_lock.release()

    def _owned_alive(self, job: dict) -> bool:
        if not job.get("pid") or job.get("process_created") is None:
            return False
        try:
            process = psutil.Process(job["pid"])
            if abs(process.create_time() - job["process_created"]) > 0.1:
                return False
            arguments = process.cmdline()
            return str(Path(job["config_path"]).resolve()).casefold() in {str(a).casefold() for a in arguments}
        except psutil.AccessDenied:
            # Do not start another GPU conversion while this PID cannot be inspected.
            return psutil.pid_exists(job["pid"])
        except (psutil.NoSuchProcess, psutil.ZombieProcess):
            return False

    def external_processes(self, fresh=False) -> list[dict]:
        stamp, cached = self.process_cache
        if not fresh and time.monotonic() - stamp < 3:
            return cached
        owned = {j.get("pid") for j in self.jobs.values() if self._owned_alive(j)}
        found = []
        for process in psutil.process_iter(["pid", "name", "cmdline", "create_time"]):
            try:
                info = process.info
                if info["pid"] in owned or info["pid"] == os.getpid():
                    continue
                name = (info["name"] or "").lower()
                if not any(word in name for word in ("python", "audiobook")):
                    continue
                args = info["cmdline"] or []
                lower = [str(a).replace("\\", "/").lower() for a in args]
                launcher = "local_audiobook.cli" in lower or (
                    "local_audiobook.jobs" in lower and "--run-job" in lower) or any(
                    Path(a).name in {"audiobook", "audiobook.exe"} for a in lower)
                for argument in args:
                    candidate = Path(str(argument))
                    if candidate.name.lower() not in {"main.py", "audiobook.py"}:
                        continue
                    absolute = candidate if candidate.is_absolute() else Path(process.cwd()) / candidate
                    if absolute.resolve().parent in {self.workspace, self.app_root}:
                        launcher = True
                if launcher and not any(flag in lower for flag in ("--dry-run", "--status", "--doctor")):
                    found.append({"pid": info["pid"], "started_at": info["create_time"],
                                  "command": " ".join(args)})
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
        self.process_cache = (time.monotonic(), found)
        return found

    def _launch(self, job: dict):
        attempt_id = uuid.uuid4().hex
        arguments = [sys.executable, "-m", "local_audiobook.jobs", "--run-job", job["id"],
                     "--app-root", str(self.app_root), "--workspace", str(self.workspace),
                     "--config", job["config_path"], "--attempt-id", attempt_id]
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(self.app_root / "src")
        environment["PYTHONUNBUFFERED"] = "1"
        environment["PYTHONUTF8"] = "1"
        log = Path(job["log_path"]).open("ab")
        flags = (subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP) if sys.platform == "win32" else 0
        launched_at = now()
        try:
            child = subprocess.Popen(arguments, cwd=self.app_root, env=environment, stdout=log,
                                     stderr=subprocess.STDOUT, creationflags=flags,
                                     start_new_session=sys.platform != "win32")
        except Exception:
            log.close()
            raise
        try:
            process_created = psutil.Process(child.pid).create_time()
        except psutil.NoSuchProcess:
            process_created = time.time()
        job.update(status="running", pid=child.pid, process_created=process_created,
                   started_at=launched_at, attempt=job["attempt"] + 1, attempt_id=attempt_id,
                   blocked_reason=None, error=None)
        self.children[job["id"]] = child
        self.log_handles[job["id"]] = log
        self._save(job)
        LOG.info("Started owned UI conversion %s with PID %s", job["id"], child.pid)

    @staticmethod
    def _speech_signature(config: Config) -> dict:
        return {"tts": config.tts.model_dump(mode="json", exclude={"batch_size"}),
                "voice": config.voice.model_dump(mode="json")}

    def _cache_candidates(self, job: dict, config: Config) -> dict[str, list[dict]]:
        candidates = defaultdict(list)
        signature = self._speech_signature(config)
        for previous in reversed(list(self.jobs.values())):
            if previous["id"] == job["id"] or previous["status"] not in TERMINAL:
                continue
            old_root = self.root / "jobs" / previous["id"]
            config_path = Path(previous["config_path"])
            runtime = Path(previous["work_root"])
            if not inside(config_path, old_root) or not inside(runtime, old_root):
                continue
            try:
                previous_config = Config.model_validate(yaml.safe_load(config_path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
            if self._speech_signature(previous_config) != signature:
                continue
            for source in job["sources"]:
                # The content hash and original filename keep unrelated books
                # from sharing speech just because a paragraph happens to match.
                matches = [old for old in previous["sources"] if
                           Path(old["name"]).stem.casefold() == Path(source["name"]).stem.casefold()]
                if not matches:
                    continue
                paths = {str(Path(old["path"]).resolve()) for old in matches}
                for manifest_path in runtime.glob("*/manifest.json"):
                    manifest = read_json(manifest_path, {})
                    if manifest.get("source") not in paths or manifest.get("source_sha256") != source["sha256"]:
                        continue
                    database = manifest_path.parent / "state.sqlite3"
                    if database.is_file():
                        candidates[source["path"]].append({"job_id": previous["id"], "work": manifest_path.parent,
                                                           "database": database, "manifest": manifest})
        return candidates

    def prepare_cache(self, job: dict) -> dict:
        """Copy compatible completed speech into this job, leaving prior jobs read-only.

        The owned queue subprocess runs preparation so large disk copies never
        block browser requests or require a second GPU worker.
        """
        report_path = self.root / "jobs" / job["id"] / "cache-reuse.json"
        previous_report = read_json(report_path, {})
        reused = set(previous_report.get("reused_ids", []))
        source_jobs = set(previous_report.get("source_jobs", []))
        copied = 0
        warnings = []
        report = {"status": "preparing", "cache_reused_segments": len(reused), "copied_this_run": 0,
                  "reused_ids": sorted(reused), "source_jobs": sorted(source_jobs), "updated_at": now()}

        def save(status):
            report.update(status=status, cache_reused_segments=len(reused), copied_this_run=copied,
                          reused_ids=sorted(reused), source_jobs=sorted(source_jobs),
                          warnings=warnings[:20], updated_at=now())
            atomic_json(report_path, report)
            return report

        save("preparing")
        stop_file = Path(job["stop_file"])
        if stop_file.exists():
            return save("paused")
        config = Config.model_validate(yaml.safe_load(Path(job["config_path"]).read_text(encoding="utf-8")))
        # Clone fingerprints currently include each job's reference path.
        # Preset voices have stable identities across isolated job snapshots.
        if config.voice.mode != "preset":
            return save("skipped")
        for source in job["sources"]:
            source["sha256"] = file_hash(Path(source["path"]))
        candidates = self._cache_candidates(job, config)
        if not candidates:
            return save("completed")
        engine = QwenEngine(config)  # Identity only; does not load Torch or a model.
        pipeline = Pipeline(config, engine=engine)
        selection = self._chapter_numbers(job.get("chapters"))
        try:
            for source in job["sources"]:
                if stop_file.exists():
                    return save("paused")
                compatible = [c for c in candidates.get(source["path"], [])
                              if c["manifest"].get("generation_fingerprint") == engine.fingerprint]
                if not compatible:
                    continue
                _, _, work, _, chapters = pipeline.plan(Path(source["path"]), selection)
                pipeline.identify_segments(chapters, work)
                expected = {s["id"]: s for c in chapters for s in c["segments"]}
                with_state = State(work / "state.sqlite3")
                try:
                    for candidate in compatible:
                        if stop_file.exists():
                            return save("paused")
                        try:
                            with sqlite3.connect(candidate["database"].resolve().as_uri() + "?mode=ro",
                                                 uri=True, timeout=1) as old_db:
                                for key, raw in old_db.execute("SELECT id,data FROM segments"):
                                    if stop_file.exists():
                                        return save("paused")
                                    if key not in expected:
                                        continue
                                    old_record = json.loads(raw)
                                    if old_record.get("status") != "completed" or not old_record.get("audio_sha256"):
                                        continue
                                    old_audio = Path(old_record.get("output_path", ""))
                                    if not inside(old_audio, candidate["work"] / "segments") or not old_audio.is_file():
                                        continue
                                    target = Path(expected[key]["output_path"])
                                    existing = with_state.get(key)
                                    if existing and existing.get("status") == "completed" and target.is_file():
                                        if file_hash(target) == existing.get("audio_sha256"):
                                            if existing.get("cache_source_job"):
                                                reused.add(f"{work.name}:{key}")
                                                source_jobs.add(existing["cache_source_job"])
                                            continue
                                    if file_hash(old_audio) != old_record["audio_sha256"]:
                                        warnings.append("A prior speech file failed its checksum and was skipped")
                                        continue
                                    target.parent.mkdir(parents=True, exist_ok=True)
                                    temporary = target.with_suffix(".cache-copy.wav")
                                    try:
                                        shutil.copyfile(old_audio, temporary)
                                        if file_hash(temporary) != old_record["audio_sha256"]:
                                            raise OSError("Speech cache copy checksum mismatch")
                                        os.replace(temporary, target)
                                        record = {**old_record, **expected[key], "output_path": str(target),
                                                  "cache_source_job": candidate["job_id"]}
                                        # Preserve the QC hash: the pipeline checks it against the new
                                        # policy and rechecks copied audio whenever the policy changed.
                                        with_state.save(key, record)
                                    finally:
                                        temporary.unlink(missing_ok=True)
                                    reused.add(f"{work.name}:{key}")
                                    source_jobs.add(candidate["job_id"])
                                    copied += 1
                                    if copied % 100 == 0:
                                        save("preparing")
                                        print(f"INFO Speech cache copied {copied} verified segments.", flush=True)
                        except (sqlite3.Error, ValueError, OSError) as exc:
                            warnings.append(f"Prior cache was unavailable: {type(exc).__name__}: {exc}")
                finally:
                    with_state.close()
        finally:
            # Identity construction leaves the model unloaded. Avoid unload(),
            # which imports Torch only to clear an unused GPU allocator.
            pipeline.engine = None
        return save("completed")

    def _finish(self, job: dict):
        child = self.children.pop(job["id"], None)
        returncode = child.poll() if child else None
        handle = self.log_handles.pop(job["id"], None)
        if handle:
            handle.close()
        result = read_json(self.root / "jobs" / job["id"] / "worker-result.json", {})
        if not job.get("attempt_id") or result.get("attempt_id") != job["attempt_id"]:
            result = {}
        if returncode is None:
            returncode = result.get("returncode")
        batch = read_json(Path(job["work_root"]) / "batch.json", {})
        # A resumed worker can fail before replacing an earlier completed batch.
        current_batch = bool(batch.get("updated_at")) and batch["updated_at"] >= (job.get("started_at") or "")
        books = batch.get("books", []) if current_batch else []
        failed = returncode not in {None, 0} or (bool(job.get("attempt_id")) and returncode is None)
        paused = Path(job["stop_file"]).exists()
        reference = read_json(Path(job["work_root"]) / "reference-result.json", {}) if job.get("kind") == "reference" else {}
        reference_ready = (reference.get("created_at", "") >= (job.get("started_at") or "")
                           and Path(reference.get("path", "")).is_file())
        if job.get("cancel_requested_at"):
            status = "cancelled"
        elif failed:
            status = "failed"
        elif reference_ready or (books and all(book.get("status") == "completed" for book in books)):
            status = "completed"
        elif paused:
            status = "paused"
        else:
            status = "failed"
        errors = [book.get("error") for book in books if book.get("error")]
        if result.get("error"):
            errors.append(result["error"])
        job.update(status=status, finished_at=now(), returncode=returncode, pid=None,
                   process_created=None, blocked_reason=None, error="; ".join(errors) or
                   ("Conversion failed; inspect the retained log" if status == "failed" else None))
        if status == "completed" and reference_ready:
            item = self.register_file(Path(reference["path"]), reference=True)
            item.update(prepared=True, duration=reference["duration"], sha256=reference["sha256"])
            atomic_json(self.files_path, self.files)
            job["reference"] = dict(item)
            job["reference_text"] = reference["text"]
            job["reference_warnings"] = reference["warnings"]
            job["reference_clips"] = reference["clips"]
        self._save(job)

    def _run(self):
        while not self.stop.wait(1):
            with self.lock:
                try:
                    active = []
                    for job in self.jobs.values():
                        if job["status"] in ACTIVE:
                            if self._owned_alive(job):
                                active.append(job)
                            else:
                                self._finish(job)
                    if active:
                        continue
                    pending = self._queued_jobs()
                    queued = pending[0] if pending else None
                    if not queued:
                        continue
                    external = self.external_processes(fresh=True)
                    if external:
                        reason = "An existing command-line conversion is running; this queue will wait"
                        if queued.get("blocked_reason") != reason:
                            queued["blocked_reason"] = reason
                            self._save(queued)
                        continue
                    if self.stop.is_set():
                        return
                    try:
                        self._launch(queued)
                    except Exception as exc:
                        queued.update(status="failed", error=f"{type(exc).__name__}: {exc}")
                        self._save(queued)
                        LOG.exception("Unable to start UI job %s", queued["id"])
                except Exception:
                    LOG.exception("UI queue iteration failed")

    def _queued_jobs(self) -> list[dict]:
        return sorted((j for j in self.jobs.values() if j["status"] == "queued"),
                      key=lambda j: (j.get("queue_order", 0), j.get("queued_at", j["created_at"]), j["id"]))

    def _enqueue(self, job: dict, *, first=False):
        positions = [j.get("queue_order", 0) for j in self._queued_jobs() if j["id"] != job["id"]]
        order = min(positions, default=0) - 1 if first else max(positions, default=0) + 1
        job.update(status="queued", queue_order=order, queued_at=now(), archived=False,
                   blocked_reason=None, started_at=None, finished_at=None, returncode=None)

    def _cancel(self, job: dict):
        if job["status"] in {"cancelled", "cancelling"}:
            return
        if job["status"] == "completed":
            raise ValueError("Completed jobs cannot be cancelled; archive the record instead")
        if job["status"] not in ACTIVE | {"queued", "paused", "failed"}:
            raise ValueError("This job cannot be cancelled")
        Path(job["stop_file"]).write_text("Cancel at the next safe conversion boundary.\n", encoding="utf-8")
        job.update(cancel_requested_at=now(), blocked_reason=None)
        if self._owned_alive(job):
            job["status"] = "cancelling"
        else:
            job.update(status="cancelled", finished_at=now(), pid=None, process_created=None)

    def cancel_queued(self) -> dict:
        with self.lock:
            cancelled = []
            for job in self._queued_jobs():
                self._cancel(job)
                self._save(job)
                cancelled.append(job["id"])
        return {"cancelled": cancelled, "count": len(cancelled)}

    def action(self, identifier: str, action: str) -> dict:
        with self.lock:
            job = self.jobs.get(identifier)
            if not job:
                raise KeyError(identifier)
            if job["status"] in ACTIVE and not self._owned_alive(job):
                self._finish(job)
            if action == "pause":
                if job["status"] == "queued":
                    job.update(status="paused", finished_at=now(), blocked_reason=None)
                elif job["status"] in {"running", "pausing"}:
                    if not self._owned_alive(job):
                        raise ValueError("The owned conversion has already exited")
                    Path(job["stop_file"]).write_text("Pause at the next safe conversion boundary.\n", encoding="utf-8")
                    job["status"] = "pausing"
                elif job["status"] != "paused":
                    raise ValueError("Only queued or running jobs can be paused")
            elif action == "cancel":
                self._cancel(job)
            elif action in {"resume", "retry"}:
                if job["status"] not in {"paused", "failed", "cancelled"}:
                    raise ValueError("Only paused, failed or cancelled jobs can be resumed")
                Path(job["stop_file"]).unlink(missing_ok=True)
                retry_failed = action == "retry" or job["status"] in {"failed", "cancelled"}
                job.update(error=None, retry_failed=retry_failed, cancel_requested_at=None)
                self._enqueue(job)
            elif action == "move-first":
                if job["status"] != "queued":
                    raise ValueError("Only queued jobs can be moved")
                self._enqueue(job, first=True)
            elif action == "archive":
                if job["status"] not in TERMINAL or self._owned_alive(job):
                    raise ValueError("Only stopped jobs can be archived")
                job["archived"] = True
            elif action == "unarchive":
                job["archived"] = False
            else:
                raise ValueError("Unknown job action")
            self._save(job)
        return self.detail(identifier)

    def _summary(self, work: Path) -> dict:
        key = str(work.resolve())
        stamp, value = self.summary_cache.get(key, (0, None))
        if value is None or time.monotonic() - stamp > 2:
            value = state_summary(work)
            self.summary_cache[key] = (time.monotonic(), value)
        return dict(value)

    def _media_records(self, job: dict, summaries: list[dict]) -> list[dict]:
        records = []
        root = Path(job["output_root"])
        for summary in summaries:
            manifest = summary.get("manifest", {})
            candidates = [(item["path"], item.get("format"), None) for item in summary["exports"]]
            for chapter in manifest.get("chapters", []):
                if chapter.get("output_path"):
                    for fmt in ("flac", "mp3", "wav"):
                        candidates.append((str(Path(chapter["output_path"]).with_suffix("." + fmt)), fmt,
                                           chapter.get("title")))
            for raw, fmt, chapter in candidates:
                path = Path(raw)
                if not inside(path, root) or not path.is_file() or path.suffix.lower() not in {
                        ".m4b", ".mp3", ".flac", ".wav"}:
                    continue
                identifier = digest(str(path.resolve()))[:20]
                records.append({"id": identifier, "name": path.name, "format": fmt or path.suffix[1:],
                                "book_title": summary["title"], "chapter": chapter, "path": str(path),
                                "export_scope": summary["export_scope"], "complete_book": summary["complete_book"],
                                "selected_chapters": summary["selected_chapters"],
                                "total_chapters": summary["total_chapters"],
                                "size": path.stat().st_size,
                                "url": f"/api/media/{job['id']}/{identifier}"})
        return records

    def detail(self, identifier: str) -> dict:
        with self.lock:
            if identifier not in self.jobs:
                raise KeyError(identifier)
            job = dict(self.jobs[identifier])
            job["queue_position"] = next((i for i, queued in enumerate(self._queued_jobs(), 1)
                                          if queued["id"] == identifier), None)
        if job.get("kind") == "reference":
            progress = read_json(Path(job["work_root"]) / "progress.json", {})
            current_progress = (job["status"] != "queued" and progress.get("updated_at", "")
                                >= (job.get("started_at") or job["created_at"]))
            completed = job["planned_total"] if job["status"] == "completed" else (
                progress.get("completed", 0) if current_progress else 0)
            job.update(completed=completed, total=job["planned_total"],
                       percent=round(100 * completed / job["planned_total"], 1), phase="reference",
                       audio_seconds=job.get("reference", {}).get("duration", 0),
                       exports=[], books=[], chapters=[], qc={}, logs=tail(Path(job["log_path"])))
            return job
        runtime = Path(job["work_root"])
        summaries = [self._summary(path.parent) for path in sorted(runtime.glob("*/plan.json"))]
        planned_total = sum(s["total"] for s in summaries)
        completed = sum(s["completed"] for s in summaries)
        audio_seconds = sum(s["audio_seconds"] for s in summaries)
        synthesized_audio_seconds = sum(s["synthesized_audio_seconds"] for s in summaries)
        generation_seconds = sum(s["generation_seconds"] for s in summaries)
        elapsed = 0.0
        if job.get("started_at"):
            start = datetime.fromisoformat(job["started_at"])
            end = datetime.fromisoformat(job["finished_at"]) if job["status"] in TERMINAL and job.get("finished_at") else datetime.now(timezone.utc)
            elapsed = max(0.0, (end - start).total_seconds())
        if "planned_total" not in job:
            # Migrate records from earlier UI versions once. Normal polling only
            # reads the totals snapshotted when the job was created.
            config = Config.model_validate(yaml.safe_load(Path(job["config_path"]).read_text(encoding="utf-8")))
            selected = self._chapter_numbers(job.get("chapters"))
            plans = []
            for item in job["sources"]:
                try:
                    plans.append(self._plan_source(config, item, selected))
                except Exception:
                    LOG.debug("Cannot migrate planning totals", exc_info=True)
            job["planned_total"] = sum(item["segments"] for item in plans) or planned_total
            job["planned_chars"] = sum(item["spoken_chars"] for item in plans)
            with self.lock:
                self.jobs[identifier].update(planned_total=job["planned_total"], planned_chars=job["planned_chars"])
                self._save(self.jobs[identifier])
        total = job["planned_total"]
        qc = {key: sum(s["qc"][key] for s in summaries) for key in ("checked", "passed", "failed", "retries")}
        records = self._media_records(job, summaries)
        if job["status"] == "completed" and total:
            percent = 100.0
        else:
            percent = round(completed / total * 100, 1) if total else 0.0
        active_summary = next((s for s in reversed(summaries) if job["status"] in ACTIVE
                               and s["status"] == "running"
                               and s.get("run_started_at") and job.get("started_at")
                               and s["run_started_at"] >= job["started_at"]), None)
        eta = speed = current_rtf = chars_per_second = None
        if active_summary:
            speed = active_summary.get("speed")
            current_rtf = active_summary.get("run_rtf")
            remaining = active_summary.get("remaining_chars")
            book_eta = active_summary.get("eta_seconds")
            if remaining and book_eta is not None:
                remaining_job = max(0, job.get("planned_chars", 0) - sum(s["chars"] for s in summaries))
                eta = book_eta / remaining * remaining_job
                chars_per_second = remaining / book_eta if book_eta else None
        historical_rtf = generation_seconds / synthesized_audio_seconds if synthesized_audio_seconds else None
        job.update(completed=completed, total=total, percent=percent, audio_seconds=audio_seconds,
                   synthesized_audio_seconds=synthesized_audio_seconds,
                   generation_seconds=generation_seconds, elapsed_seconds=elapsed, eta_seconds=eta,
                   rtf=current_rtf if active_summary else historical_rtf, historical_rtf=historical_rtf,
                   rtf_source=("current_run" if current_rtf is not None else "unavailable") if active_summary else
                   ("historical" if historical_rtf is not None else "unavailable"),
                   speed=speed, chars_per_second=chars_per_second,
                   chapters=[{**c, "book_title": s["title"]} for s in summaries for c in s["chapters"]],
                   books=[{k: v for k, v in s.items() if k not in {"manifest", "exports"}} for s in summaries],
                   qc=qc, exports=[{k: v for k, v in r.items() if k != "path"} for r in records],
                   logs=tail(Path(job["log_path"])))
        cache_report = read_json(self.root / "jobs" / identifier / "cache-reuse.json", {})
        job["cache_reused_segments"] = cache_report.get("cache_reused_segments", 0)
        job["cache_reuse_status"] = cache_report.get("status")
        if cache_report.get("status") == "preparing" and job["status"] == "running":
            job["phase"] = "reusing"
        if active_summary:
            job["phase"] = active_summary.get("phase")
            job["active_batch_size"] = active_summary.get("active_batch_size")
        if job["status"] in {"cancelling", "cancelled"}:
            job["phase"] = job["status"]
            job["eta_seconds"] = None
        return job

    def list_jobs(self) -> list[dict]:
        with self.lock:
            identifiers = sorted(self.jobs, key=lambda key: (self.jobs[key]["created_at"], key), reverse=True)
        return [self.detail(identifier) for identifier in identifiers]

    def media_path(self, identifier: str, file_id: str) -> Path:
        with self.lock:
            if identifier not in self.jobs:
                raise KeyError(identifier)
            job = dict(self.jobs[identifier])
        summaries = [self._summary(path.parent) for path in Path(job["work_root"]).glob("*/plan.json")]
        match = next((r for r in self._media_records(job, summaries) if r["id"] == file_id), None)
        if not match:
            raise KeyError(file_id)
        return Path(match["path"])

    def _legacy_paths(self) -> list[Path]:
        legacy = self.workspace / "work"
        return sorted((path for path in legacy.glob("*/progress.json")
                       if inside(path, legacy) and not inside(path, self.root)),
                      key=lambda path: path.stat().st_mtime, reverse=True)

    def monitor_action(self, identifier: str, action: str) -> dict:
        """Remove or restore display records; never edit an external conversion."""
        if action not in {"archive", "unarchive"}:
            raise ValueError("Unknown monitor record action")
        with self.lock:
            if not any("legacy-" + path.parent.name == identifier for path in self._legacy_paths()):
                raise KeyError(identifier)
            if action == "archive":
                if self.external_processes(fresh=True):
                    raise ValueError("Stop the external conversion before removing its display record")
                self.removed_monitors[identifier] = now()
            else:
                self.removed_monitors.pop(identifier, None)
            atomic_json(self.monitor_records_path, self.removed_monitors)
            return {"id": identifier, "archived": identifier in self.removed_monitors, "read_only": True}

    def monitor(self, include_removed: bool = False) -> dict:
        tasks = []
        legacy = self.workspace / "work"
        processes = self.external_processes()
        with self.lock:
            removed = set(self.removed_monitors)
        for path in self._legacy_paths():
            identifier = "legacy-" + path.parent.name
            archived = identifier in removed
            if archived and not include_removed:
                continue
            summary = self._summary(path.parent)
            summary.pop("manifest", None)
            summary.update(id=identifier, read_only=True, archived=archived, can_remove=not processes,
                           logs=tail(legacy / "pipeline.log", 12))
            tasks.append(summary)
            if len(tasks) == 20:
                break
        return {"tasks": tasks, "external_processes": processes, "queue_blocked": bool(processes)}


def run_owned_job(app_root: Path, workspace: Path, identifier: str, config_path: Path) -> int:
    """Prepare isolated speech caches, then run the unchanged converter CLI."""
    manager = JobManager(app_root, workspace)
    if identifier not in manager.jobs:
        raise ValueError("The owned queue job does not exist")
    job = manager.jobs[identifier]
    if Path(job["config_path"]).resolve() != config_path.resolve():
        raise ValueError("The worker configuration does not match its saved job")
    if Path(job["stop_file"]).exists():
        return 0
    if job.get("kind") == "reference":
        config = Config.model_validate(yaml.safe_load(config_path.read_text(encoding="utf-8")))
        clips = read_json(manager.root / "jobs" / identifier / "reference-inputs.json", [])
        directory = Path(job["work_root"])
        directory.mkdir(parents=True, exist_ok=True)
        atomic_json(directory / "progress.json", {"completed": 0, "total": len(clips), "updated_at": now()})
        worker = ASRWorker(config.quality, directory / "recognition.log") if any(
            not clip.get("text", "").strip() for clip in clips) else None
        try:
            prepare_references(AudioTools(config), clips, directory,
                               transcribe=worker.transcribe if worker else None,
                               stopped=lambda: Path(job["stop_file"]).exists(),
                               progress=lambda done, total: atomic_json(directory / "progress.json",
                                   {"completed": done, "total": total, "updated_at": now()}))
        finally:
            if worker:
                worker.close()
        return 0
    try:
        report = manager.prepare_cache(job)
        print(f"INFO Reused {report['cache_reused_segments']} verified speech segments from prior UI jobs.", flush=True)
        for warning in report.get("warnings", []):
            print(f"WARNING {warning}", flush=True)
    except Exception as exc:
        # Cache sharing is optional. A failure must not discard the new job's
        # isolated state or prevent normal resumable generation.
        print(f"WARNING Speech cache reuse was skipped: {type(exc).__name__}: {exc}", flush=True)
        report_path = manager.root / "jobs" / identifier / "cache-reuse.json"
        report = read_json(report_path, {})
        report.update(status="unavailable", error=f"{type(exc).__name__}: {exc}", updated_at=now())
        atomic_json(report_path, report)
    if Path(job["stop_file"]).exists():
        return 0
    from .cli import main as convert
    arguments = [job["input_root"], "--config", job["config_path"], "--stop-file", job["stop_file"]]
    if job.get("chapters"):
        arguments.extend(["--chapters", job["chapters"]])
    if job.get("retry_failed"):
        arguments.append("--retry-failed")
    return convert(arguments)


def worker_main() -> int:
    import argparse
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Owned local audiobook queue worker")
    parser.add_argument("--run-job", required=True)
    parser.add_argument("--app-root", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--attempt-id")
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-f]{16}", args.run_job):
        parser.error("The job ID must contain 16 lowercase hexadecimal characters")
    if args.attempt_id and not re.fullmatch(r"[0-9a-f]{32}", args.attempt_id):
        parser.error("The attempt ID must contain 32 lowercase hexadecimal characters")
    error = None
    try:
        returncode = run_owned_job(args.app_root, args.workspace, args.run_job, args.config)
    except KeyboardInterrupt:
        returncode = 130
        error = "Conversion interrupted"
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        print(error, file=sys.stderr)
        returncode = 1
    directory = args.app_root / "work" / "ui" / "jobs" / args.run_job
    if args.attempt_id and directory.is_dir():
        try:
            atomic_json(directory / "worker-result.json", {"attempt_id": args.attempt_id,
                        "returncode": returncode, "error": error, "finished_at": now()})
        except OSError as exc:
            print(f"WARNING Unable to retain worker result: {exc}", file=sys.stderr)
    return returncode


if __name__ == "__main__":
    raise SystemExit(worker_main())
