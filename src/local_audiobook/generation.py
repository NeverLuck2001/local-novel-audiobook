"""Bounded batch staging, with resumable audio and serial QC/retakes."""
import logging
from pathlib import Path

from .util import file_hash, now

LOG = logging.getLogger(__name__)
METRIC_KEYS = {"generation_seconds", "duration", "sample_rate", "rtf", "seed", "peak_vram_gb",
               "batch_size", "batch_seed", "batch_id", "batch_seconds", "batch_audio_seconds"}


def initial_attempt(prior, retry_failed, qc_hash):
    if not prior or retry_failed or prior.get("status") == "completed" or prior.get("qc_hash") != qc_hash:
        return 0
    count = prior.get("attempts", 0)
    if prior.get("status") in {"running", "generated"}:
        count = max(0, count - 1)
    return count


def staged_path(segment):
    return Path(segment["output_path"]).with_suffix(".generated.wav")


def staged_valid(record):
    if not record or record.get("status") != "generated" or not record.get("staged_sha256"):
        return False
    path = staged_path(record)
    try:
        return path.is_file() and file_hash(path) == record["staged_sha256"]
    except OSError:
        return False


class BatchStager:
    def __init__(self, engine, state, size, qc_hash, retry_failed, check_stop=None, on_generate=None):
        self.engine = engine
        self.state = state
        self.size = size if hasattr(engine, "generate_batch") else 1
        self.qc_hash = qc_hash
        self.retry_failed = retry_failed
        self.check_stop = check_stop
        self.on_generate = on_generate
        self.metrics_this_run = []
        self.generated_ids = set()

    def prefetch(self, segments, offset, remaining=None):
        if self.check_stop:
            self.check_stop()
        first = segments[offset]
        if self.size <= 1 or staged_valid(self.state.get(first["id"])):
            return
        limit = self.size if remaining is None else min(self.size, remaining)
        group, seen = [], set()
        # Keep lengths close so a short request does not wait through excessive
        # padding. Generation order may differ, chapter output order never does.
        for segment in segments[offset:offset + self.size * 4]:
            key = segment["id"]
            if key in seen:
                continue
            seen.add(key)
            prior = self.state.get(key)
            if staged_valid(prior) or initial_attempt(prior, self.retry_failed, self.qc_hash) > 0:
                continue
            path = Path(segment["output_path"])
            if prior and prior.get("status") == "completed":
                try:
                    if path.is_file() and file_hash(path) == prior.get("audio_sha256"):
                        continue
                except OSError:
                    pass
            first_text = first.get("spoken_text", first["text"])
            current_text = segment.get("spoken_text", segment["text"])
            length_ratio = max(len(first_text), len(current_text)) / max(1, min(len(first_text), len(current_text)))
            if length_ratio > 1.6:
                continue
            group.append(segment)
            if len(group) >= limit:
                break
        if len(group) > 1 and group[0]["id"] == first["id"]:
            self._generate(group)

    def _generate(self, group):
        if self.check_stop:
            self.check_stop()
        if self.on_generate:
            self.on_generate(group)
        for segment in group:
            history = (self.state.get(segment["id"]) or {}).get("attempt_history", [])
            self.state.save(segment["id"], {**segment, "status": "running", "attempts": 1,
                            "retry_count": 0, "generation_seed": segment["seed"], "qc_hash": self.qc_hash,
                            "attempt_history": history})
        paths = [staged_path(segment) for segment in group]
        LOG.info("Generating GPU batch of %d segments (%d characters)",
                 len(group), sum(len(s["text"]) for s in group))
        try:
            metrics = self.engine.generate_batch([s.get("spoken_text", s["text"]) for s in group],
                                                 paths, [s["seed"] for s in group])
            if len(metrics) != len(group):
                raise RuntimeError("Batch metrics count does not match requests")
            # Hash every result before persisting any as ready, including
            # adapters which accidentally return missing or partial files.
            hashes = [file_hash(path) for path in paths]
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            LOG.warning("Batch of %d failed (%s); reducing batch and isolating requests", len(group), exc)
            self.engine.recover()
            self.size = min(self.size, max(1, len(group) // 2))
            for segment, path in zip(group, paths, strict=True):
                path.unlink(missing_ok=True)
                history = list((self.state.get(segment["id"]) or {}).get("attempt_history", []))
                history.append({"stage": "batch_generation", "batch_size": len(group),
                                "error": f"{type(exc).__name__}: {exc}", "at": now()})
                self.state.save(segment["id"], {**segment, "status": "pending", "attempts": 0,
                                "qc_hash": self.qc_hash, "batch_error": str(exc),
                                "attempt_history": history[-32:]})
            if len(group) > 2:
                middle = len(group) // 2
                for smaller in (group[:middle], group[middle:]):
                    if len(smaller) > 1:
                        self._generate(smaller)
            return
        for segment, metric, sha in zip(group, metrics, hashes, strict=True):
            history = (self.state.get(segment["id"]) or {}).get("attempt_history", [])
            self.state.save(segment["id"], {**segment, **metric, "status": "generated", "attempts": 1,
                            "retry_count": 0, "generation_seed": segment["seed"], "qc_hash": self.qc_hash,
                            "staged_sha256": sha, "attempt_history": history})
            self.metrics_this_run.append(metric)
            self.generated_ids.add(segment["id"])
        LOG.info("Batch generated %d segments; %.2fs audio, aggregate RTF=%.3f; checking each segment",
                 len(group), sum(m["duration"] for m in metrics),
                 sum(m["generation_seconds"] for m in metrics) / max(.001, sum(m["duration"] for m in metrics)))
