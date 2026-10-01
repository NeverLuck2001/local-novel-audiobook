"""FFmpeg-only processing, bounded disk intermediates, and tagged exports."""
import io
import json
import math
import os
from pathlib import Path
import re
import subprocess

from PIL import Image

from .config import Config
from .util import atomic_json, digest, file_hash


class AudioTools:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._rubberband = None

    def supports_rubberband(self) -> bool:
        if self._rubberband is None:
            result = subprocess.run([self.cfg.ffmpeg, "-hide_banner", "-filters"], capture_output=True,
                                    text=True, encoding="utf-8", errors="replace", timeout=30,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if result.returncode:
                raise RuntimeError("Cannot inspect available FFmpeg filters")
            self._rubberband = bool(re.search(r"\brubberband\s+A->A", result.stdout))
        return self._rubberband

    def delivery_filter(self) -> str:
        output = self.cfg.output
        if output.speech_rate == 1.0 and output.pitch_semitones == 0.0:
            return ""
        if self.supports_rubberband():
            pitch = 2 ** (output.pitch_semitones / 12)
            return f"rubberband=tempo={output.speech_rate:g}:pitch={pitch:.9f},"
        if output.pitch_semitones:
            raise ValueError("Pitch adjustment requires an FFmpeg build with the rubberband filter")
        return f"atempo={output.speech_rate:g},"

    def validate_delivery(self):
        # Fail before model loading rather than after an entire chapter is generated.
        self.delivery_filter()

    def run(self, args: list[str], timeout: int = 21600) -> str:
        result = subprocess.run([self.cfg.ffmpeg, "-hide_banner", "-nostdin", "-y", *args],
                                capture_output=True, text=True, encoding="utf-8", errors="replace",
                                timeout=timeout, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode:
            raise RuntimeError(f"FFmpeg failed ({result.returncode}): {result.stderr[-4000:]}")
        return result.stderr

    def probe(self, path: Path) -> dict:
        result = subprocess.run([self.cfg.ffprobe, "-v", "error", "-show_format", "-show_streams",
                                 "-show_chapters", "-of", "json", str(path)], capture_output=True,
                                text=True, encoding="utf-8", errors="replace", timeout=60,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode:
            raise RuntimeError(f"Unreadable audio {path}: {result.stderr[-2000:]}")
        return json.loads(result.stdout)

    @staticmethod
    def concat_list(path: Path, files: list[Path]):
        def escaped(file):
            return str(file.resolve()).replace("\\", "/").replace("'", "'\\''")
        path.write_text("ffconcat version 1.0\n" + "".join(f"file '{escaped(p)}'\n" for p in files),
                        encoding="utf-8")

    def prepare(self, path: Path, destination: Path, boundary: str):
        output = self.cfg.output
        pause = {"paragraph": output.paragraph_pause, "sentence": output.sentence_pause,
                 "clause": output.clause_pause, "hard": output.clause_pause,
                 "chapter": output.chapter_pause}[boundary]
        filters = ("silenceremove=start_periods=1:start_duration=0.02:start_threshold=-50dB:start_silence=0.06,"
                   "areverse,silenceremove=start_periods=1:start_duration=0.02:"
                   "start_threshold=-50dB:start_silence=0.08,areverse,"
                   f"{self.delivery_filter()}"
                   f"apad=pad_dur={pause}")
        temporary = destination.with_suffix(".tmp.flac")
        self.run(["-i", str(path), "-af", filters, "-ar", str(output.sample_rate), "-ac", "1",
                  "-c:a", "flac", str(temporary)], timeout=600)
        self.probe(temporary)
        os.replace(temporary, destination)

    def normalize_chapter(self, files: list[Path], destination: Path, work: Path) -> dict:
        playlist = work / "segments.ffconcat"
        self.concat_list(playlist, files)
        raw = work / "chapter.raw.flac"
        self.run(["-f", "concat", "-safe", "0", "-i", str(playlist), "-c:a", "copy", str(raw)])
        out = self.cfg.output
        loudness = f"loudnorm=I={out.loudness_lufs}:TP={out.true_peak_db}:LRA={out.loudness_range}"
        report = self.run(["-i", str(raw), "-af", loudness + ":print_format=json", "-f", "null", "-"])
        matches = re.findall(r'\{\s*"input_i".*?\}', report, re.S)
        if not matches:
            raise RuntimeError("FFmpeg loudness measurements missing")
        measured = json.loads(matches[-1])
        if not all(math.isfinite(float(measured[k])) for k in ["input_i", "input_tp", "input_lra", "input_thresh"]):
            raise RuntimeError("Nonfinite loudness measurement; chapter is likely silent")
        second = (loudness + f":measured_I={measured['input_i']}:measured_TP={measured['input_tp']}"
                  f":measured_LRA={measured['input_lra']}:measured_thresh={measured['input_thresh']}"
                  f":offset={measured['target_offset']}:linear=true:print_format=json")
        temporary = destination.with_suffix(".tmp.flac")
        self.run(["-i", str(raw), "-af", second, "-ar", str(out.sample_rate), "-ac", "1",
                  "-c:a", "flac", str(temporary)])
        info = self.probe(temporary)
        os.replace(temporary, destination)
        raw.unlink(missing_ok=True)
        atomic_json(work / "loudness.json", measured)
        return {"duration": float(info["format"]["duration"]), "loudness": measured}

    def chapter_format(self, flac: Path, fmt: str, title: str, author: str):
        if fmt == "flac":
            return flac
        destination = flac.with_suffix("." + fmt)
        temporary = destination.with_name(destination.stem + ".tmp." + fmt)
        codec = ["-c:a", "pcm_s16le", "-rf64", "auto"] if fmt == "wav" else ["-c:a", "libmp3lame", "-b:a", self.cfg.output.bitrate]
        self.run(["-i", str(flac), *codec, "-metadata", f"title={title}",
                  "-metadata", f"artist={author}", str(temporary)])
        self.probe(temporary)
        os.replace(temporary, destination)
        return destination

    @staticmethod
    def _meta(text):
        return str(text).replace("\\", "\\\\").replace("=", "\\=").replace(";", "\\;").replace("#", "\\#").replace("\n", " ").replace("\r", " ")

    def book(self, chapters: list[dict], title: str, author: str, cover: bytes | None,
             directory: Path, basename: str, work: Path, fmt: str) -> Path:
        playlist = work / "chapters.ffconcat"
        self.concat_list(playlist, [Path(chapter["output_path"]) for chapter in chapters])
        metadata = work / "chapters.ffmetadata"
        lines = [";FFMETADATA1", f"title={self._meta(title)}", f"artist={self._meta(author)}",
                 f"album={self._meta(title)}", f"album_artist={self._meta(author)}", "genre=Audiobook"]
        elapsed = 0.0
        for chapter in chapters:
            end = elapsed + chapter["duration"]
            lines += ["[CHAPTER]", "TIMEBASE=1/1000", f"START={round(elapsed*1000)}", f"END={round(end*1000)}",
                      f"title={self._meta(chapter['title'])}"]
            elapsed = end
        metadata.write_text("\n".join(lines) + "\n", encoding="utf-8")
        destination = directory / f"{basename}.{fmt}"
        temporary = destination.with_name(destination.stem + f".tmp.{fmt}")
        args = ["-f", "concat", "-safe", "0", "-i", str(playlist), "-f", "ffmetadata", "-i", str(metadata)]
        use_cover = False
        if cover and fmt == "m4b":
            cover_file = work / "cover.jpg"
            with Image.open(io.BytesIO(cover)) as image:
                image.thumbnail((1600, 1600))
                image.convert("RGB").save(cover_file, "JPEG", quality=90)
            args += ["-i", str(cover_file)]
            use_cover = True
        args += ["-map", "0:a:0", "-map_metadata", "1", "-map_chapters", "1"]
        if fmt == "m4b":
            args += ["-c:a", "aac", "-b:a", self.cfg.output.bitrate]
            if use_cover:
                args += ["-map", "2:v:0", "-c:v", "mjpeg", "-disposition:v:0", "attached_pic"]
            args += ["-movflags", "+faststart", "-f", "mp4"]
        else:
            args += ["-c:a", "libmp3lame", "-b:a", self.cfg.output.bitrate]
        self.run([*args, str(temporary)])
        probe = self.probe(temporary)
        if abs(float(probe["format"]["duration"]) - elapsed) > max(1, elapsed * .001):
            raise RuntimeError("Export duration differs from complete chapter duration")
        if fmt == "m4b" and len(probe.get("chapters", [])) != len(chapters):
            raise RuntimeError("M4B chapter markers missing")
        os.replace(temporary, destination)
        return destination


def artifact_valid(record: dict | None, fingerprint: str, paths: list[Path] | None = None) -> bool:
    if not record or record.get("fingerprint") != fingerprint:
        return False
    try:
        recorded = record["files"]
        if paths and set(map(str, paths)) != set(recorded):
            return False
        return all(Path(path).is_file() and file_hash(Path(path)) == sha for path, sha in recorded.items())
    except (OSError, KeyError):
        return False


def artifact_record(fingerprint: str, paths: list[Path], **extra):
    return {"fingerprint": fingerprint, "files": {str(path): file_hash(path) for path in paths}, **extra}


def render_fingerprint(cfg: Config, records: list[dict], title: str) -> str:
    return digest({"audio_version": 1, "output": render_settings(cfg),
                   "segments": [(record["id"], record["audio_sha256"], record["boundary"]) for record in records],
                   "title": title})


def render_settings(cfg: Config) -> dict:
    """Keep neutral new controls out of existing render/cache fingerprints."""
    settings = cfg.output.model_dump(mode="json", exclude={"root"})
    for name, default in {"speech_rate": 1.0, "pitch_semitones": 0.0}.items():
        if settings[name] == default:
            settings.pop(name)
    return settings
