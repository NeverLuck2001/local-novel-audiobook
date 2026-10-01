"""Validated configuration and paths relative to the configuration file."""

from pathlib import Path
import re

from pydantic import BaseModel, ConfigDict, Field, model_validator
import yaml


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SegmentConfig(StrictModel):
    target_chars: int = Field(default=200, ge=20, le=800)
    max_chars: int = Field(default=300, ge=40, le=1000)
    min_chars: int = Field(default=60, ge=1)

    @model_validator(mode="after")
    def bounds(self):
        if not self.min_chars <= self.target_chars <= self.max_chars:
            raise ValueError("Expected min_chars <= target_chars <= max_chars")
        return self


class VoiceConfig(StrictModel):
    mode: str = "preset"
    speaker: str = "Serena"
    language: str = "Chinese"
    instruct: str = "自然、温柔、平静的小说旁白，语速稍慢，避免夸张表演。"
    reference_audio: Path | None = None
    reference_text: str | None = None

    @model_validator(mode="after")
    def validate_mode(self):
        if self.mode not in {"preset", "clone"}:
            raise ValueError("voice.mode must be preset or clone; design a reference voice separately")
        if self.mode == "clone" and (not self.reference_audio or not self.reference_text):
            raise ValueError("Cloning requires reference_audio and its exact reference_text")
        if self.mode == "clone" and self.instruct:
            raise ValueError("Base cloning does not support instruct; set voice.instruct to an empty string")
        return self


class TTSConfig(StrictModel):
    model_path: Path = Path("models/Qwen3-TTS-12Hz-1.7B-CustomVoice")
    device: str = "cuda:0"
    dtype: str = "bfloat16"
    attention: str = "sdpa"
    seed: int = Field(default=20261001, ge=0, le=2**31 - 1)
    temperature: float = Field(default=0.8, gt=0, le=2)
    top_p: float = Field(default=0.95, gt=0, le=1)
    top_k: int = Field(default=50, ge=0)
    repetition_penalty: float = Field(default=1.05, ge=1, le=2)
    max_new_tokens: int = Field(default=2048, ge=128, le=8192)
    do_sample: bool = True
    # Execution policy; completed audio remains reusable when this is changed.
    batch_size: int = Field(default=1, ge=1, le=16)

    @model_validator(mode="after")
    def supported(self):
        if self.dtype not in {"bfloat16", "float16", "float32"}:
            raise ValueError("Unsupported TTS dtype")
        if self.attention not in {"sdpa", "eager", "flash_attention_2"}:
            raise ValueError("Unsupported attention implementation")
        if self.device != "cpu" and not re.fullmatch(r"cuda(?::\d+)?", self.device):
            raise ValueError("device must be cpu or cuda:N")
        return self

    def generation_args(self):
        return self.model_dump(include={"temperature", "top_p", "top_k", "repetition_penalty",
                                        "max_new_tokens", "do_sample"})


class QualityConfig(StrictModel):
    max_retry: int = Field(default=3, ge=0, le=20)
    min_duration: float = Field(default=0.4, gt=0)
    min_chars_per_second: float = Field(default=0.7, gt=0)
    max_chars_per_second: float = Field(default=14, gt=0)
    max_silence_seconds: float = Field(default=3, gt=0)
    silence_db: float = Field(default=-45, ge=-90, le=-10)
    max_clipping_ratio: float = Field(default=0.02, ge=0, le=1)
    asr_check: bool = False
    asr_python: Path = Path(".venv-asr/Scripts/python.exe")
    asr_model_path: Path = Path("models/Qwen3-ASR-0.6B")
    asr_timeout: int = Field(default=300, ge=10)
    max_cer: float = Field(default=0.15, ge=0, le=1)


class TextConfig(StrictModel):
    encoding: str | None = None
    chapter_pattern: str = r"^(?:第[零〇一二三四五六七八九十百千万两\d]+[章节回卷部篇](?:\s*.*)?|Chapter\s+\d+\b.*|序章(?:\s+.*)?|楔子(?:\s+.*)?|尾声(?:\s+.*)?)$"
    remove_urls: bool = False
    strip_front_matter: bool = False
    remove_line_patterns: list[str] = []
    pronunciation_map: dict[str, str] = {}
    join_wrapped_lines: bool = False
    exclude_chapter_patterns: list[str] = []

    @model_validator(mode="after")
    def patterns(self):
        re.compile(self.chapter_pattern, re.I)
        for pattern in self.exclude_chapter_patterns + self.remove_line_patterns:
            re.compile(pattern)
        for source, spoken in self.pronunciation_map.items():
            if not source.strip() or not spoken.strip():
                raise ValueError("Pronunciation replacements require non-empty source and spoken text")
            if len(source) > 200 or len(spoken) > 400:
                raise ValueError("Pronunciation replacements must remain short (source <= 200, spoken <= 400)")
        return self


class OutputConfig(StrictModel):
    root: Path = Path("output")
    chapter_formats: list[str] = ["flac"]
    m4b: bool = True
    mp3: bool = False
    bitrate: str = "128k"
    loudness_lufs: float = Field(default=-18, ge=-30, le=-9)
    true_peak_db: float = Field(default=-1.5, ge=-9, le=0)
    loudness_range: float = Field(default=7, ge=1, le=20)
    sentence_pause: float = Field(default=0.18, ge=0, le=3)
    paragraph_pause: float = Field(default=0.45, ge=0, le=5)
    clause_pause: float = Field(default=0.08, ge=0, le=2)
    chapter_pause: float = Field(default=1, ge=0, le=5)
    sample_rate: int = Field(default=24000, ge=16000, le=48000)
    speech_rate: float = Field(default=1.0, ge=0.75, le=1.5)
    pitch_semitones: float = Field(default=0.0, ge=-3, le=3)

    @model_validator(mode="after")
    def formats(self):
        if not self.chapter_formats or set(self.chapter_formats) - {"wav", "flac", "mp3"}:
            raise ValueError("chapter_formats must contain wav, flac and/or mp3")
        if not re.fullmatch(r"\d+k", self.bitrate):
            raise ValueError("bitrate must use the form 128k")
        return self


class Config(StrictModel):
    work_root: Path = Path("work")
    ffmpeg: str = "ffmpeg"
    ffprobe: str = "ffprobe"
    tts: TTSConfig = Field(default_factory=TTSConfig)
    voice: VoiceConfig = Field(default_factory=VoiceConfig)
    segment: SegmentConfig = Field(default_factory=SegmentConfig)
    text: TextConfig = Field(default_factory=TextConfig)
    quality: QualityConfig = Field(default_factory=QualityConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)


def load_config(path: Path) -> Config:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    config = Config.model_validate(data)
    base = path.resolve().parent
    for obj, attrs in [(config, ["work_root"]), (config.tts, ["model_path"]),
                       (config.voice, ["reference_audio"]), (config.output, ["root"]),
                       (config.quality, ["asr_python", "asr_model_path"])]:
        for attr in attrs:
            value = getattr(obj, attr)
            if value is not None:
                setattr(obj, attr, (base / value).resolve())
    return config
