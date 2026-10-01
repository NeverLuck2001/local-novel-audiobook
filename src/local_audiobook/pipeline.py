"""Unattended, per-segment resumable pipeline with independent book failures."""
import json
import logging
import os
from pathlib import Path
import time

from filelock import FileLock

from .audio import AudioTools, artifact_valid, artifact_record, render_fingerprint, render_settings
from .asr import ASRWorker
from .config import Config
from .engine import QwenEngine
from .generation import BatchStager, METRIC_KEYS, initial_attempt, staged_path, staged_valid
from .parser import parse_book
from .quality import check_audio, compare_text
from .state import State
from .text import segment_text, spoken_text
from .util import atomic_json, digest, file_hash, now, safe_name

LOG = logging.getLogger(__name__)


class StopRequested(Exception):
    pass


def manifest_signature(manifest):
    config = manifest.get("config", {})
    # Batch size is an execution policy, not a reason to archive valid exports.
    config = {**config, "tts": {k: v for k, v in config.get("tts", {}).items() if k != "batch_size"}}
    for group, additions in {"output": {"speech_rate": 1.0, "pitch_semitones": 0.0},
                             "text": {"strip_front_matter": False, "remove_line_patterns": [],
                                      "pronunciation_map": {}}}.items():
        config[group] = dict(config.get(group, {}))
        for name, default in additions.items():
            if config[group].get(name, default) == default:
                config[group].pop(name, None)
    signature = {"source": manifest.get("source_sha256"), "config": config,
                 "generator": manifest.get("generation_fingerprint")}
    if manifest.get("chapter_selection"):
        signature["chapter_selection"] = manifest["chapter_selection"]
    return digest(signature)


class Pipeline:
    def __init__(self, cfg: Config, engine=None):
        self.cfg = cfg
        self.engine = engine
        self.audio = AudioTools(cfg)
        self.asr = None

    def plan(self, source: Path, chapter_indexes: set[int] | None = None):
        book = parse_book(source, self.cfg.text)
        book_id = safe_name(source.stem, 40) + "-" + digest(str(source.resolve()))[:10]
        work = self.cfg.work_root / book_id
        output = self.cfg.output.root / book_id
        chapters = []
        for index, chapter in enumerate(book.chapters, 1):
            if chapter_indexes is not None and index not in chapter_indexes:
                continue
            parts = segment_text(chapter.text, self.cfg.segment)
            segments = []
            for part_index, part in enumerate(parts, 1):
                segment = vars(part).copy()
                spoken, replacements = spoken_text(part.text, self.cfg.text.pronunciation_map)
                if spoken != part.text:
                    if len(spoken) > 1000:
                        raise ValueError("Pronunciation replacements expanded a segment beyond 1000 characters")
                    segment.update(spoken_text=spoken, spoken_text_hash=digest(spoken))
                    book.audit.append({"operation": "pronunciation_replacements", "chapter": index,
                                       "segment": part_index, "replacements": replacements})
                segments.append(segment)
            chapters.append({"index": index, "title": chapter.title, "source": chapter.source,
                             "text": chapter.text, "segments": segments})
        if chapter_indexes is not None:
            invalid = sorted(chapter_indexes - set(range(1, len(book.chapters) + 1)))
            if invalid:
                raise ValueError(f"Chapter indexes outside 1..{len(book.chapters)}: {invalid}")
            if not chapters:
                raise ValueError("Chapter selection is empty")
        return book, book_id, work, output, chapters

    def identify_segments(self, chapters: list[dict], work: Path) -> list[dict]:
        """Attach stable cache identities without loading or running a model."""
        if self.engine is None:
            raise ValueError("A configured engine is required to identify segments")
        for chapter in chapters:
            for index, segment in enumerate(chapter["segments"], 1):
                input_text = segment.get("spoken_text", segment["text"])
                segment["seed"] = (self.cfg.tts.seed + int(digest({"text": input_text,
                                   "chapter": chapter["title"]})[:8], 16)) % (2**31 - 1)
                segment["id"] = digest({"generation": self.engine.fingerprint, "text": input_text,
                                        "seed": segment["seed"]})
                segment["text_hash"] = digest(segment["text"])
                segment["index"] = index
                segment["output_path"] = str(work / "segments" / self.engine.fingerprint[:16] / (segment["id"] + ".wav"))
        return chapters

    def convert(self, source: Path, *, max_segments: int | None = None, retry_failed: bool = False,
                stop_file: Path | None = None, chapter_indexes: set[int] | None = None) -> dict:
        book, book_id, work, output, chapters = self.plan(source, chapter_indexes)
        work.mkdir(parents=True, exist_ok=True)
        with FileLock(str(work / "task.lock"), timeout=0):
            return self._convert(source, book, book_id, work, output, chapters, max_segments, retry_failed,
                                 stop_file, chapter_indexes)

    def _convert(self, source, book, book_id, work, output, chapters, max_segments, retry_failed,
                 stop_file=None, chapter_indexes=None):
        output.mkdir(parents=True, exist_ok=True)
        text_dir = work / "text_clean"
        text_dir.mkdir(exist_ok=True)
        atomic_json(text_dir / "audit.json", book.audit)
        for chapter in chapters:
            (text_dir / f"{chapter['index']:05d}.txt").write_text(chapter["text"], encoding="utf-8")
            if any("spoken_text" in segment for segment in chapter["segments"]):
                (text_dir / f"{chapter['index']:05d}.spoken.txt").write_text(
                    "\n\n".join(segment.get("spoken_text", segment["text"]) for segment in chapter["segments"]),
                    encoding="utf-8")
        if book.cover:
            (work / "cover.original").write_bytes(book.cover)
        self.audio.validate_delivery()
        if self.engine is None:
            self.engine = QwenEngine(self.cfg)
        if self.cfg.quality.asr_check and self.asr is None:
            self.asr = ASRWorker(self.cfg.quality, work / "asr-worker.log")
            # Preflight ASR before generating thousands of unusable segments.
            self.asr.start()
        qc_hash = digest({"qc_version": 1, "quality": self.cfg.quality.model_dump(mode="json",
                         exclude={"max_retry", "asr_python", "asr_model_path", "asr_timeout"}),
                         "asr_model": self.asr.identity["sha256"] if self.asr else None})
        self.identify_segments(chapters, work)
        manifest = {"schema_version": 1, "book_id": book_id, "source": str(source.resolve()),
                    "source_sha256": file_hash(source), "title": book.title, "author": book.author,
                    "model": self.engine.identity, "engine_versions": self.engine.versions,
                    "generation_fingerprint": self.engine.fingerprint,
                    "config": self.cfg.model_dump(mode="json"), "status": "running",
                    "started_at": now(), "chapters": chapters, "exports": []}
        manifest.update(total_chapters=len(book.chapters), selected_chapters=[c["index"] for c in chapters],
                        complete_book=len(chapters) == len(book.chapters),
                        export_scope="complete_book" if len(chapters) == len(book.chapters) else "selected_chapters")
        if chapter_indexes is not None:
            manifest["chapter_selection"] = sorted(chapter_indexes)
        previous_path = output / "manifest.json"
        if previous_path.exists():
            previous = json.loads(previous_path.read_text(encoding="utf-8"))
            current_signature = manifest_signature(manifest)
            previous_signature = manifest_signature(previous)
            if current_signature != previous_signature:
                # Preserve prior successful exports, but do not present them as the new run's complete book.
                archive = output / "previous" / previous_signature[:16]
                for export in previous.get("exports", []):
                    path = Path(export["path"])
                    if path.parent.resolve() == output.resolve() and path.is_file():
                        archive.mkdir(parents=True, exist_ok=True)
                        os.replace(path, archive / path.name)
                if archive.exists():
                    atomic_json(archive / "manifest.json", previous)
        atomic_json(work / "plan.json", manifest)
        atomic_json(work / "manifest.json", manifest)
        state = State(work / "state.sqlite3")
        completed_chapters = []
        failures = []
        generated = skipped = 0
        stats = []
        total = sum(len(chapter["segments"]) for chapter in chapters)
        processed = 0
        started = time.perf_counter()
        flat_segments = [segment for chapter in chapters for segment in chapter["segments"]]
        completed_keys = {segment["id"] for segment in flat_segments
                          if (state.get(segment["id"]) or {}).get("status") == "completed"
                          and Path(segment["output_path"]).is_file()}
        remaining_chars, segment_counts = {}, {}
        for segment in flat_segments:
            key = segment["id"]
            remaining_chars[key] = remaining_chars.get(key, 0) + len(segment.get("spoken_text", segment["text"]))
            segment_counts[key] = segment_counts.get(key, 0) + 1
        generated_chars = 0
        current = {"chapter": None, "segment": None}

        def check_stop():
            if stop_file is not None and stop_file.exists():
                raise StopRequested("Pause requested; rerun the same command to resume")

        def progress(phase, *, status="running", batch_size=None):
            elapsed = time.perf_counter() - started
            synthesis = stats + stager.metrics_this_run
            generation_seconds = sum(item["generation_seconds"] for item in synthesis)
            audio_seconds = sum(item["duration"] for item in synthesis)
            remaining = sum(chars for key, chars in remaining_chars.items() if key not in completed_keys)
            seconds_per_char = elapsed / generated_chars if generated_chars else None
            snapshot = {"status": status, "phase": phase, **current, "processed": processed,
                        "total": total, "completed": sum(segment_counts[key] for key in completed_keys), "generated": generated,
                        "synthesized_segments": len(synthesis), "export_scope": manifest["export_scope"],
                        "cached": skipped, "failed": len(failures), "updated_at": now(),
                        "started_at": manifest["started_at"], "elapsed_seconds": elapsed,
                        "generation_seconds": generation_seconds, "audio_seconds": audio_seconds,
                        "rtf": generation_seconds / audio_seconds if audio_seconds else None,
                        "audio_seconds_per_second": audio_seconds / elapsed if elapsed else None,
                        "segments_per_minute": generated * 60 / elapsed if elapsed else None,
                        "eta_seconds": remaining * seconds_per_char if seconds_per_char is not None else None,
                        "eta_estimated": True, "remaining_chars": remaining}
            if batch_size is not None:
                snapshot["active_batch_size"] = batch_size
            atomic_json(work / "progress.json", snapshot)

        stager = BatchStager(self.engine, state, self.cfg.tts.batch_size, qc_hash, retry_failed,
                             check_stop=check_stop, on_generate=lambda group: progress(
                                 "generating", batch_size=len(group)))
        try:
            check_stop()
            progress("starting")
            for chapter in chapters:
                records = []
                chapter_failed = False
                for segment_offset, segment in enumerate(chapter["segments"]):
                    check_stop()
                    current.update(chapter=chapter["index"], segment=segment["index"])
                    processed += 1
                    key = segment["id"]
                    path = Path(segment["output_path"])
                    prior = state.get(key)
                    input_text = segment.get("spoken_text", segment["text"])
                    record = {**segment, "status": "pending", "attempts": 0}
                    valid = bool(prior and prior.get("status") == "completed" and path.is_file())
                    if valid:
                        try:
                            valid = file_hash(path) == prior.get("audio_sha256")
                        except OSError:
                            valid = False
                    if valid:
                        record = {**prior, **segment}
                        if record.get("qc_hash") != qc_hash:
                            progress("checking")
                            qc = self._check(path, input_text)
                            valid = qc["pass"]
                            record.update(qc=qc, qc_hash=qc_hash)
                            if valid:
                                state.save(key, record)
                        if valid:
                            skipped += 1
                            LOG.info("Cached %s chapter=%d segment=%d [%d/%d]", book_id,
                                     chapter["index"], segment["index"], processed, total)
                    # Exhausted failures are persistent unless explicitly retried or QC changed.
                    exhausted = (prior and prior.get("status") == "failed" and prior.get("qc_hash") == qc_hash
                                 and prior.get("attempts", 0) >= self.cfg.quality.max_retry + 1)
                    if not valid and exhausted and not retry_failed:
                        record = {**prior, **segment}
                    elif not valid:
                        completed_keys.discard(key)
                        if max_segments is not None and generated >= max_segments:
                            raise StopRequested("Reached --max-segments checkpoint")
                        attempts = initial_attempt(prior, retry_failed, qc_hash)
                        if attempts == 0:
                            stager.prefetch(chapter["segments"], segment_offset,
                                            None if max_segments is None else max_segments - generated)
                        staged = state.get(key)
                        history = list((staged or prior or {}).get("attempt_history", []))
                        for attempt in range(attempts, self.cfg.quality.max_retry + 1):
                            check_stop()
                            seed = (segment["seed"] + attempt) % (2**31 - 1)
                            record = {**segment, "status": "running", "attempts": attempt + 1,
                                      "retry_count": attempt, "generation_seed": seed, "qc_hash": qc_hash}
                            if history:
                                record["attempt_history"] = history
                            state.save(key, record)
                            temporary = path.with_suffix(".tmp.wav")
                            try:
                                if attempt == attempts and staged_valid(staged):
                                    metrics = {k: v for k, v in staged.items() if k in METRIC_KEYS}
                                    newly_synthesized = key in stager.generated_ids
                                    os.replace(staged_path(staged), temporary)
                                else:
                                    progress("generating", batch_size=1)
                                    metrics = self.engine.generate(input_text, temporary, seed)
                                    newly_synthesized = True
                                    stats.append(metrics)
                                progress("checking")
                                qc = self._check(temporary, input_text)
                                record.update(metrics, qc=qc)
                                if not qc["pass"]:
                                    raise ValueError("QC failed: " + ", ".join(qc["reasons"]))
                                os.replace(temporary, path)
                                record.update(status="completed", audio_sha256=file_hash(path), error=None)
                                state.save(key, record)
                                generated += 1
                                if newly_synthesized:
                                    generated_chars += len(input_text)
                                completed_keys.add(key)
                                LOG.info("Generated %s chapter=%d segment=%d [%d/%d] RTF=%.3f",
                                         book_id, chapter["index"], segment["index"], processed, total, metrics["rtf"])
                                break
                            except (KeyboardInterrupt, StopRequested):
                                raise
                            except Exception as exc:
                                temporary.unlink(missing_ok=True)
                                record.update(status="failed", error=f"{type(exc).__name__}: {exc}")
                                summary = {"attempt": attempt + 1, "seed": seed, "error": record["error"],
                                           "at": now(), "reasons": record.get("qc", {}).get("reasons", [])}
                                if record.get("qc", {}).get("asr"):
                                    comparison = record["qc"]["asr"]
                                    summary["asr"] = {key: comparison[key] for key in ["cer", "insertions",
                                                       "deletions", "substitutions", "transcription"]}
                                history.append(summary)
                                record["attempt_history"] = history[-32:]
                                state.save(key, record)
                                LOG.warning("Segment failure chapter=%d segment=%d %d/%d: %s%s",
                                            chapter["index"], segment["index"], attempt + 1,
                                            self.cfg.quality.max_retry + 1, record["error"],
                                            " CER=%.3f" % record["qc"]["asr"]["cer"]
                                            if record.get("qc", {}).get("asr") else "")
                                self.engine.recover()
                    if record["status"] != "completed":
                        chapter_failed = True
                        failures.append({"chapter": chapter["index"], **record})
                    records.append(record)
                    if record["status"] == "completed":
                        completed_keys.add(key)
                    progress("checkpoint")
                    check_stop()
                chapter["segments"] = records
                if chapter_failed:
                    chapter["status"] = "failed"
                    continue
                try:
                    check_stop()
                    progress("rendering")
                    item = self._chapter(chapter, work, output, state, book.author, check_stop=check_stop)
                    completed_chapters.append(item)
                    chapter.update(item, status="completed")
                except StopRequested:
                    raise
                except Exception as exc:
                    chapter.update(status="failed", error=f"{type(exc).__name__}: {exc}")
                    failures.append({"chapter": chapter["index"], "stage": "chapter_export", "error": chapter["error"]})
                    LOG.exception("Chapter export failed; continuing")
            if not failures and len(completed_chapters) == len(chapters):
                check_stop()
                progress("exporting")
                self._export(book, completed_chapters, work, output, state, manifest, check_stop=check_stop)
                manifest["status"] = "completed"
            else:
                manifest["status"] = "failed"
        except StopRequested as exc:
            manifest.update(status="pending", error=str(exc))
        except KeyboardInterrupt:
            manifest.update(status="pending", error="Interrupted; rerun the same command to resume")
            raise
        except Exception as exc:
            manifest.update(status="failed", error=f"{type(exc).__name__}: {exc}")
            failures.append({"stage": "book_export", "error": manifest["error"]})
            raise
        finally:
            # Refresh only once, including partially processed chapters after an interruption.
            for chapter in chapters:
                chapter["segments"] = [{**(state.get(segment["id"]) or {}), **segment,
                                        "index": segment["index"], "boundary": segment["boundary"]}
                                       for segment in chapter["segments"]]
            synthesis = stats + stager.metrics_this_run
            gen_seconds = sum(item["generation_seconds"] for item in synthesis)
            audio_seconds = sum(item["duration"] for item in synthesis)
            manifest.update(updated_at=now(), generated_this_run=generated, cached_this_run=skipped,
                            elapsed_this_run=time.perf_counter() - started,
                            benchmark={"generation_seconds": gen_seconds, "audio_seconds": audio_seconds,
                                       "synthesized_segments": len(synthesis),
                                       "rtf": gen_seconds / audio_seconds if audio_seconds else None,
                                       "peak_vram_gb": max((s.get("peak_vram_gb", 0) for s in synthesis), default=0)})
            state.close()
            atomic_json(work / "manifest.json", manifest)
            atomic_json(output / "manifest.json", manifest)
            atomic_json(output / "failures.json", failures)
            atomic_json(output / "qc-report.json", [{"chapter": c["index"], "segment": s["index"],
                        "id": s["id"], "status": s.get("status", "pending"), "qc": s.get("qc"),
                        "error": s.get("error"), "attempt_history": s.get("attempt_history", [])}
                        for c in chapters for s in c["segments"]])
            progress(manifest["status"], status=manifest["status"])
        return manifest

    def _check(self, path, text):
        qc = check_audio(path, text, self.cfg.quality)
        if qc["pass"] and self.asr:
            comparison = compare_text(text, self.asr.transcribe(path))
            qc["asr"] = comparison
            if comparison["cer"] > self.cfg.quality.max_cer:
                qc["pass"] = False
                qc["reasons"].append("asr_cer_exceeded")
        return qc

    def _chapter(self, chapter, work, output, state, author, check_stop=None):
        records = chapter["segments"]
        fingerprint = render_fingerprint(self.cfg, records, chapter["title"])
        target_dir = output / "chapters"
        target_dir.mkdir(exist_ok=True)
        flac = target_dir / f"{chapter['index']:05d}.flac"
        formats = set(self.cfg.output.chapter_formats) | {"flac"}
        paths = [flac.with_suffix("." + fmt) for fmt in sorted(formats)]
        key = f"chapter:{chapter['index']}"
        prior = state.get(key, "artifacts")
        if artifact_valid(prior, fingerprint, paths):
            return {"title": chapter["title"], "output_path": str(flac), "duration": prior["duration"],
                    "audio_sha256": file_hash(flac), "loudness": prior["loudness"]}
        prepared = work / "prepared" / f"{chapter['index']:05d}"
        prepared.mkdir(parents=True, exist_ok=True)
        segment_paths = []
        for i, record in enumerate(records):
            if check_stop:
                check_stop()
            destination = prepared / f"{i:05d}.flac"
            prep_fp = digest({"version": 1, "sha": record["audio_sha256"], "boundary": record["boundary"],
                              "output": render_settings(self.cfg)})
            prep_key = f"prepared:{chapter['index']}:{i}"
            if not artifact_valid(state.get(prep_key, "artifacts"), prep_fp, [destination]):
                self.audio.prepare(Path(record["output_path"]), destination, record["boundary"])
                state.save(prep_key, artifact_record(prep_fp, [destination]), "artifacts")
            segment_paths.append(destination)
        if check_stop:
            check_stop()
        details = self.audio.normalize_chapter(segment_paths, flac, prepared)
        for fmt in formats:
            if check_stop:
                check_stop()
            self.audio.chapter_format(flac, fmt, chapter["title"], author)
        state.save(key, artifact_record(fingerprint, paths, **details), "artifacts")
        return {"title": chapter["title"], "output_path": str(flac), "audio_sha256": file_hash(flac), **details}

    def _export(self, book, chapters, work, output, state, manifest, check_stop=None):
        for fmt, enabled in [("m4b", self.cfg.output.m4b), ("mp3", self.cfg.output.mp3)]:
            if check_stop:
                check_stop()
            if not enabled:
                continue
            fingerprint = digest({"version": 1, "chapters": chapters, "title": book.title, "author": book.author,
                                  "cover": digest(book.cover.hex()) if book.cover else None,
                                  "bitrate": self.cfg.output.bitrate})
            destination = output / f"{safe_name(book.title)}.{fmt}"
            key = "book:" + fmt
            if not artifact_valid(state.get(key, "artifacts"), fingerprint, [destination]):
                self.audio.book(chapters, book.title, book.author, book.cover, output,
                                safe_name(book.title), work, fmt)
                state.save(key, artifact_record(fingerprint, [destination]), "artifacts")
            manifest["exports"].append({"format": fmt, "path": str(destination), "sha256": file_hash(destination)})

    def close(self):
        if self.asr:
            self.asr.close()
        if self.engine:
            self.engine.unload()
