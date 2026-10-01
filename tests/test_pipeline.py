from pathlib import Path
import shutil

import numpy as np
import pytest
import soundfile as sf
from filelock import FileLock, Timeout

from local_audiobook.config import Config
from local_audiobook.pipeline import Pipeline
from local_audiobook.quality import check_audio, compare_text
from local_audiobook.state import State
from local_audiobook.util import file_hash


class FixtureEngine:
    """Synthetic tone fixture only; never used as a runtime TTS backend."""
    identity = {"sha256": "fixture-model", "revision": "test"}
    versions = {"fixture": "1"}

    def __init__(self, fingerprint="voice-a", fail_text=None, interrupt_after=None):
        self.fingerprint = fingerprint
        self.fail_text = fail_text
        self.interrupt_after = interrupt_after
        self.calls = []

    def generate(self, text, path, seed):
        if self.interrupt_after is not None and len(self.calls) >= self.interrupt_after:
            raise KeyboardInterrupt
        self.calls.append((text, seed))
        if self.fail_text and self.fail_text in text:
            raise RuntimeError("Injected generation failure")
        path.parent.mkdir(parents=True, exist_ok=True)
        x = np.arange(48000) / 24000
        sf.write(path, 0.2 * np.sin(2 * np.pi * 220 * x) * (0.8 + 0.2 * np.sin(x * 4)), 24000)
        return {"generation_seconds": .1, "duration": 2, "rtf": .05, "seed": seed, "peak_vram_gb": 0}

    def recover(self):
        pass

    def unload(self):
        pass


@pytest.fixture
def settings(tmp_path):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg is required for integration tests")
    cfg = Config(work_root=tmp_path / "work")
    cfg.output.root = tmp_path / "output"
    cfg.quality.max_chars_per_second = 1000
    cfg.quality.min_chars_per_second = .01
    cfg.quality.max_retry = 1
    return cfg


@pytest.fixture
def novel(tmp_path):
    path = tmp_path / "novel's sample.txt"
    path.write_text("第一章 来信\n" + "雨停了，她收到一封信。" * 35 + "\n第二章 河边\n" + "她在河边看到了白色小屋。" * 30, encoding="utf-8")
    return path


def test_pause_resume_and_no_reencode(settings, novel):
    first = FixtureEngine()
    paused = Pipeline(settings, first).convert(novel, max_segments=1)
    assert paused["status"] == "pending"
    cached_path = Path(paused["chapters"][0]["segments"][0]["output_path"])
    before = cached_path.stat().st_mtime_ns
    second = FixtureEngine()
    pipeline = Pipeline(settings, second)
    finished = pipeline.convert(novel)
    assert finished["status"] == "completed"
    assert finished["cached_this_run"] == 1
    assert cached_path.stat().st_mtime_ns == before
    m4b = Path(finished["exports"][0]["path"])
    info = pipeline.audio.probe(m4b)
    assert len(info["chapters"]) == 2
    assert info["format"]["tags"]["title"] == novel.stem
    mtime = m4b.stat().st_mtime_ns
    again = pipeline.convert(novel)
    assert again["generated_this_run"] == 0
    assert m4b.stat().st_mtime_ns == mtime


def test_corrupt_audio_regenerates_only_one_segment(settings, novel):
    engine = FixtureEngine()
    pipe = Pipeline(settings, engine)
    finished = pipe.convert(novel)
    segment = Path(finished["chapters"][0]["segments"][0]["output_path"])
    segment.write_bytes(b"broken")
    before_calls = len(engine.calls)
    again = pipe.convert(novel)
    assert again["status"] == "completed"
    assert len(engine.calls) - before_calls == 1


def test_voice_and_text_changes_invalidate(settings, novel):
    first = Pipeline(settings, FixtureEngine()).convert(novel)
    other = Pipeline(settings, FixtureEngine("voice-b")).convert(novel)
    assert other["generated_this_run"] == sum(len(c["segments"]) for c in other["chapters"])
    assert first["generation_fingerprint"] != other["generation_fingerprint"]
    novel.write_text(novel.read_text(encoding="utf-8").replace("白色小屋", "蓝色小屋"), encoding="utf-8")
    updated = Pipeline(settings, FixtureEngine("voice-b")).convert(novel)
    assert updated["generated_this_run"] > 0
    assert updated["cached_this_run"] > 0


def test_exhausted_failure_persists_and_other_chapter_continues(settings, novel):
    engine = FixtureEngine(fail_text="来信")
    pipe = Pipeline(settings, engine)
    first = pipe.convert(novel)
    assert first["status"] == "failed" and first["exports"] == []
    assert first["chapters"][1]["status"] == "completed"
    failed = first["chapters"][0]["segments"][0]
    assert failed["attempts"] == 2
    assert engine.calls[0][1] != engine.calls[1][1]
    count = len(engine.calls)
    pipe.convert(novel)
    assert len(engine.calls) == count
    recovered = Pipeline(settings, FixtureEngine()).convert(novel, retry_failed=True)
    assert recovered["status"] == "completed"


def test_keyboard_interrupt_recovers_without_losing_completed_segments(settings, novel):
    with pytest.raises(KeyboardInterrupt):
        Pipeline(settings, FixtureEngine(interrupt_after=1)).convert(novel)
    recovered = Pipeline(settings, FixtureEngine()).convert(novel)
    assert recovered["status"] == "completed"
    assert recovered["cached_this_run"] == 1


def test_same_book_lock_prevents_concurrent_writers(settings, novel):
    pipeline = Pipeline(settings, FixtureEngine())
    _, _, work, _, _ = pipeline.plan(novel)
    work.mkdir(parents=True)
    with FileLock(str(work / "task.lock")):
        with pytest.raises(Timeout):
            pipeline.convert(novel)


def test_qc_policy_changes_rechecks_without_generating(settings, novel):
    engine = FixtureEngine()
    pipe = Pipeline(settings, engine)
    pipe.convert(novel)
    settings.quality.max_silence_seconds = 4
    count = len(engine.calls)
    finished = pipe.convert(novel)
    assert finished["status"] == "completed"
    assert len(engine.calls) == count


def test_render_change_does_not_regenerate_tts(settings, novel):
    engine = FixtureEngine()
    pipe = Pipeline(settings, engine)
    first = pipe.convert(novel)
    count = len(engine.calls)
    settings.output.loudness_lufs = -21
    changed = pipe.convert(novel)
    assert changed["status"] == "completed" and len(engine.calls) == count
    assert first["exports"][0]["sha256"] != changed["exports"][0]["sha256"]


def test_all_output_formats(settings, novel):
    settings.output.chapter_formats = ["wav", "flac", "mp3"]
    settings.output.mp3 = True
    manifest = Pipeline(settings, FixtureEngine()).convert(novel)
    assert manifest["status"] == "completed"
    assert {e["format"] for e in manifest["exports"]} == {"m4b", "mp3"}
    for chapter in manifest["chapters"]:
        flac = Path(chapter["output_path"])
        assert all(flac.with_suffix("." + fmt).is_file() for fmt in ["wav", "mp3", "flac"])


def test_audio_qc_rejects_silence_and_cer_detects_missing_repeated_text(tmp_path):
    path = tmp_path / "silence.wav"
    sf.write(path, np.zeros(24000 * 5), 24000)
    qc = check_audio(path, "这是需要正常阅读的小说内容。", Config().quality)
    assert not qc["pass"] and "silent_audio" in qc["reasons"]
    assert compare_text("窗外，雨停了。", "窗外雨停了")["cer"] == 0
    assert compare_text("窗外雨停了", "窗外")["deletions"] > 0
    assert compare_text("雨停了", "雨停了雨停了")["insertions"] > 0


def test_transaction_state_is_persistent(tmp_path):
    path = tmp_path / "state.sqlite3"
    state = State(path)
    state.save("segment", {"status": "completed", "sha": "abc"})
    state.close()
    recovered = State(path)
    assert recovered.get("segment")["status"] == "completed"
    recovered.close()


def test_hash_detects_content_change_even_with_same_length(tmp_path):
    path = tmp_path / "reference.wav"
    path.write_bytes(b"abcd")
    original = file_hash(path)
    path.write_bytes(b"efgh")
    assert file_hash(path) != original


def test_asr_missing_text_triggers_retake_and_passes(settings, tmp_path):
    path = tmp_path / "short.txt"
    source = "窗外雨停了，她打开窗户，看见街上的行人。"
    path.write_text(source, encoding="utf-8")

    class FirstMissingASR:
        identity = {"sha256": "asr-fixture"}
        calls = 0

        def transcribe(self, audio):
            assert audio.is_file()
            self.calls += 1
            return "窗外雨停了" if self.calls == 1 else source

    engine = FixtureEngine()
    pipe = Pipeline(settings, engine)
    pipe.asr = FirstMissingASR()
    manifest = pipe.convert(path)
    assert manifest["status"] == "completed"
    assert len(engine.calls) == 2
    segment = manifest["chapters"][0]["segments"][0]
    assert segment["qc"]["asr"]["cer"] == 0 and segment["retry_count"] == 1


def test_changed_source_failure_archives_old_complete_book(settings, novel):
    finished = Pipeline(settings, FixtureEngine()).convert(novel)
    old = Path(finished["exports"][0]["path"])
    original_sha = file_hash(old)
    novel.write_text(novel.read_text(encoding="utf-8").replace("来信", "FAIL"), encoding="utf-8")
    failed = Pipeline(settings, FixtureEngine(fail_text="FAIL")).convert(novel)
    assert failed["status"] == "failed" and not old.exists()
    archived = list((old.parent / "previous").rglob("*.m4b"))
    assert len(archived) == 1 and file_hash(archived[0]) == original_sha
