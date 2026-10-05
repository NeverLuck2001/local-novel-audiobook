"""Bounded local reference preparation for reusable Qwen Base voice prompts."""
from pathlib import Path
import math
import subprocess

import numpy as np
import soundfile as sf

from .audio import AudioTools
from .util import atomic_json, file_hash, now

REFERENCE_RATE = 24000
MAX_REFERENCE_SECONDS = 60
MAX_SOURCE_SECONDS = 600
MAX_REFERENCE_CLIPS = 5
VIDEO_SUFFIXES = {".mp4", ".m4v", ".mov", ".mkv", ".webm", ".avi"}
MAX_REFERENCE_UPLOAD_BYTES = 64 * 1024 * 1024
MAX_VIDEO_UPLOAD_BYTES = 512 * 1024 * 1024


def extract_video_audio(audio: AudioTools, source: Path, target: Path) -> float:
    """Extract the first audio track for playback and the existing reference workflow."""
    try:
        probe = audio.probe(source)
        if not any(stream.get("codec_type") == "audio" for stream in probe.get("streams", [])):
            raise ValueError("The video has no audio track; choose a video containing speech")
        try:
            duration = float(probe["format"]["duration"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("The video duration is invalid; choose another video") from exc
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError("The video duration is invalid; choose another video")
        if duration > MAX_SOURCE_SECONDS:
            raise ValueError("Each source video must be no longer than 10 minutes; select a shorter file")
        audio.run(["-i", str(source), "-map", "0:a:0", "-vn", "-sn", "-dn",
                   "-t", str(MAX_SOURCE_SECONDS), "-ac", "1", "-ar", str(REFERENCE_RATE),
                   "-c:a", "pcm_s16le", str(target)], timeout=180)
        wave_info = sf.info(target)
        if wave_info.frames <= 0 or wave_info.samplerate != REFERENCE_RATE or wave_info.channels != 1:
            raise ValueError("The video cannot be decoded into usable audio; choose another video")
        return round(wave_info.duration, 3)
    except (RuntimeError, subprocess.TimeoutExpired) as exc:
        raise ValueError("The video cannot be decoded into usable audio; choose another video") from exc



def prepare_references(audio: AudioTools, clips: list[dict], directory: Path,
                       transcribe=None, stopped=lambda: False, progress=None) -> dict | None:
    """Keep words intact, trim edge silence only, and concatenate matching transcripts.

    Each input is an immutable job snapshot. The official API's list of references
    represents separate prompts; concatenate same-speaker clips into one prompt.
    """
    if not 1 <= len(clips) <= MAX_REFERENCE_CLIPS:
        raise ValueError("Select between 1 and 5 recordings of the same speaker")
    directory.mkdir(parents=True, exist_ok=True)
    samples, records, texts, warnings = [], [], [], []
    selected_seconds = 0.0
    for index, clip in enumerate(clips, 1):
        if stopped():
            return None
        source = Path(clip["path"])
        probe = audio.probe(source)
        try:
            duration = float(probe["format"]["duration"])
            start = float(clip.get("start", 0))
            end = duration if clip.get("end") is None else float(clip["end"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("The recording duration or selected time range is invalid") from exc
        if not all(math.isfinite(v) for v in (duration, start, end)) or not 0 <= start < end <= duration + 0.05:
            raise ValueError("Select a valid start and end within the recording")
        if duration > MAX_SOURCE_SECONDS:
            raise ValueError("Each source recording must be no longer than 10 minutes; select a shorter file")
        selected_seconds += end - start
        if selected_seconds > MAX_REFERENCE_SECONDS:
            raise ValueError("The selected recordings exceed 60 seconds; shorten the selected ranges")
        target = directory / f"clip-{index:02d}.wav"
        audio.run(["-ss", str(start), "-i", str(source), "-t", str(end - start),
                   "-vn", "-ac", "1", "-ar", str(REFERENCE_RATE), "-c:a", "pcm_s16le", str(target)],
                  timeout=90)
        wave, rate = sf.read(target, dtype="float32")
        if wave.ndim != 1 or not len(wave) or rate != REFERENCE_RATE or not np.isfinite(wave).all():
            raise ValueError("The recording cannot be decoded into a usable mono reference")
        peak = float(np.max(np.abs(wave)))
        if peak < 0.001:
            raise ValueError("The selected recording is silent or too quiet; choose a clearer recording")
        clipped = float(np.mean(np.abs(wave) >= 0.995))
        if clipped > 0.001:
            warnings.append({"code": "clipping", "clip": index})
        # Pad threshold crossings to preserve consonants and breaths at the edges.
        audible = np.flatnonzero(np.abs(wave) > max(0.002, peak * 0.015))
        if not len(audible):
            raise ValueError("No usable speech level was found in the selected recording")
        pad = int(0.15 * rate)
        first, last = max(0, int(audible[0]) - pad), min(len(wave), int(audible[-1]) + pad + 1)
        wave = wave[first:last]
        length = len(wave) / rate
        if length < 1:
            raise ValueError("Each selected clip must contain at least one second of usable audio")
        rms = float(np.sqrt(np.mean(wave ** 2)))
        if rms < 0.01:
            warnings.append({"code": "quiet", "clip": index})
        if float(np.mean(np.abs(wave) < 0.003)) > 0.7:
            warnings.append({"code": "silence", "clip": index})
        # Conservative gain only; do not denoise, compress, or change the timbre.
        wave = wave * min(4.0, 0.8 / peak)
        sf.write(target, wave, rate, subtype="PCM_16")
        text = str(clip.get("text") or "").strip()
        automatic = not text
        if automatic:
            if transcribe is None:
                raise ValueError("Enter the exact transcript or install the local ASR environment")
            text = transcribe(target).strip()
        if not text:
            raise ValueError("Local recognition returned no speech; enter the exact transcript manually")
        if len(text) > 3000:
            raise ValueError("Each reference transcript must contain at most 3000 characters")
        records.append({"name": clip["name"], "start": start + first / rate,
                        "end": start + last / rate, "duration": round(length, 3),
                        "text": text, "automatic": automatic, "source_sha256": file_hash(source),
                        "peak": round(peak, 4), "rms": round(rms, 4), "clipped_fraction": clipped})
        samples.append(wave)
        texts.append(text)
        if progress:
            progress(index, len(clips))
    if stopped():
        return None
    gap = np.zeros(int(REFERENCE_RATE * 0.2), dtype="float32")
    combined = np.concatenate([part for i, wave in enumerate(samples)
                               for part in ((gap, wave) if i else (wave,))])
    duration = len(combined) / REFERENCE_RATE
    if duration > MAX_REFERENCE_SECONDS:
        raise ValueError("The combined reference exceeds 60 seconds including pauses; shorten the ranges")
    if duration < 3:
        warnings.append({"code": "short"})
    if duration > 30:
        warnings.append({"code": "long"})
    target = directory / "reference.wav"
    sf.write(target, combined, REFERENCE_RATE, subtype="PCM_16")
    result = {"path": str(target.resolve()), "text": "\n".join(texts), "clips": records,
              "duration": round(duration, 3), "sample_rate": REFERENCE_RATE,
              "sha256": file_hash(target), "warnings": warnings, "created_at": now()}
    atomic_json(directory / "reference-result.json", result)
    return result
