"""Acoustic QC and character error rate. No source text is rewritten."""
import re
import unicodedata
from pathlib import Path

import numpy as np
import soundfile as sf
from rapidfuzz.distance import Levenshtein

from .config import QualityConfig


def normalized_text(text: str) -> str:
    return "".join(c.lower() for c in unicodedata.normalize("NFKC", text) if c.isalnum())


def compare_text(source: str, transcription: str) -> dict:
    reference, hypothesis = normalized_text(source), normalized_text(transcription)
    ops = Levenshtein.editops(reference, hypothesis)
    counts = {"insert": 0, "delete": 0, "replace": 0}
    for operation in ops:
        counts[operation.tag] += 1
    distance = sum(counts.values())
    return {"source_normalized": reference, "transcription": transcription,
            "transcription_normalized": hypothesis, "cer": distance / max(1, len(reference)),
            "similarity": max(0, 1 - distance / max(1, len(reference), len(hypothesis))),
            "insertions": counts["insert"], "deletions": counts["delete"], "substitutions": counts["replace"]}


def check_audio(path: Path, text: str, cfg: QualityConfig) -> dict:
    audio, rate = sf.read(path, dtype="float32", always_2d=True)
    reasons = []
    if not len(audio) or not np.all(np.isfinite(audio)):
        return {"pass": False, "reasons": ["empty_or_nonfinite_audio"]}
    duration = len(audio) / rate
    chars = len(normalized_text(text))
    speed = chars / max(duration, 0.001)
    if duration < cfg.min_duration:
        reasons.append("too_short")
    if speed > cfg.max_chars_per_second:
        reasons.append("possible_missing_text_or_too_short")
    if chars > 5 and speed < cfg.min_chars_per_second:
        reasons.append("possible_repetition_or_too_long")
    peak = float(np.max(np.abs(audio)))
    clipping = float(np.mean(np.abs(audio) >= 0.999))
    if peak < 10**(cfg.silence_db / 20):
        reasons.append("silent_audio")
    if clipping > cfg.max_clipping_ratio:
        reasons.append("excessive_clipping")
    mono = audio.mean(axis=1)
    size = max(1, round(rate * 0.02))
    frames = len(mono) // size
    rms = np.sqrt(np.mean(mono[:frames * size].reshape(frames, size)**2, axis=1)) if frames else np.array([])
    silent = rms < 10**(cfg.silence_db / 20)
    runs = re.findall(r"1+", "".join("1" if value else "0" for value in silent))
    max_silence = max((len(run) * 0.02 for run in runs), default=0)
    if max_silence > cfg.max_silence_seconds:
        reasons.append("long_silence")
    return {"pass": not reasons, "reasons": reasons, "duration": duration, "sample_rate": rate,
            "channels": audio.shape[1], "peak": peak, "clipping_ratio": clipping,
            "max_silence_seconds": max_silence, "chars_per_second": speed}
