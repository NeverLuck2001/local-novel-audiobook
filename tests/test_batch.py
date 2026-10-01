"""Batch ordering, checkpoint recovery, retakes and failure isolation."""
from pathlib import Path

import pytest

from local_audiobook.engine import QwenEngine
from local_audiobook.generation import BatchStager, staged_path, staged_valid
from local_audiobook.pipeline import Pipeline
from local_audiobook.state import State
from local_audiobook.util import file_hash
from test_pipeline import FixtureEngine, settings as pipeline_settings


@pytest.fixture
def settings(tmp_path):
    return pipeline_settings.__wrapped__(tmp_path)


class BatchFixture(FixtureEngine):
    def __init__(self, *args, fail_batch_over=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.batches = []
        self.fail_batch_over = fail_batch_over

    def generate_batch(self, texts, paths, seeds):
        self.batches.append(list(texts))
        if self.fail_batch_over is not None and len(texts) > self.fail_batch_over:
            raise RuntimeError("Injected GPU OOM")
        return [self.generate(text, path, seed) for text, path, seed in zip(texts, paths, seeds, strict=True)]


@pytest.fixture
def batch_novel(tmp_path):
    path = tmp_path / "batch.txt"
    sentences = ["清晨她推开窗户，看见街道上的行人。", "午后他走进书店，拿起桌上的一本书。",
                 "傍晚小船停在岸边，船夫正在收起绳子。", "夜里远处传来钟声，院子里安静了下来。"]
    path.write_text("第一章 一天\n" + "\n\n".join(text * 10 for text in sentences), encoding="utf-8")
    return path


def test_batch_outputs_keep_order_and_cached_audio_survives_size_change(settings, batch_novel):
    settings.tts.batch_size = 4
    engine = BatchFixture()
    pipe = Pipeline(settings, engine)
    expected = [s["text"] for c in pipe.plan(batch_novel)[4] for s in c["segments"]]
    manifest = pipe.convert(batch_novel)
    assert manifest["status"] == "completed" and engine.batches
    records = [s for c in manifest["chapters"] for s in c["segments"]]
    assert [s["text"] for s in records] == expected
    assert all(s["status"] == "completed" and s["qc"]["pass"] for s in records)
    count = len(engine.calls)
    settings.tts.batch_size = 1
    again = pipe.convert(batch_novel)
    assert again["status"] == "completed" and len(engine.calls) == count
    assert again["cached_this_run"] == len(records)


def test_batch_max_segments_never_runs_ahead_of_checkpoint(settings, batch_novel):
    settings.tts.batch_size = 4
    engine = BatchFixture()
    first = Pipeline(settings, engine).convert(batch_novel, max_segments=1)
    assert first["status"] == "pending" and len(engine.calls) == 1 and not engine.batches
    second = Pipeline(settings, BatchFixture()).convert(batch_novel)
    assert second["status"] == "completed" and second["cached_this_run"] == 1


def test_interruption_during_batch_qc_reuses_staged_outputs(settings, batch_novel, monkeypatch):
    settings.tts.batch_size = 4
    pipe = Pipeline(settings, BatchFixture())
    original = pipe._check
    calls = 0

    def interrupt_second(path, text):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise KeyboardInterrupt
        return original(path, text)

    monkeypatch.setattr(pipe, "_check", interrupt_second)
    with pytest.raises(KeyboardInterrupt):
        pipe.convert(batch_novel)
    state_path = pipe.plan(batch_novel)[2] / "state.sqlite3"
    state = State(state_path)
    import json
    generated = [json.loads(row[0]) for row in state.db.execute("SELECT data FROM segments")
                 if json.loads(row[0])["status"] == "generated"]
    assert generated and all(staged_valid(s) for s in generated)
    hashes = {s["id"]: file_hash(staged_path(s)) for s in generated}
    state.close()
    recovered_engine = BatchFixture()
    recovered = Pipeline(settings, recovered_engine).convert(batch_novel)
    assert recovered["status"] == "completed" and recovered["cached_this_run"] == 1
    for record in recovered["chapters"][0]["segments"]:
        if record["id"] in hashes:
            assert file_hash(Path(record["output_path"])) == hashes[record["id"]]
            assert record["text"] not in [text for text, _ in recovered_engine.calls]


def test_batch_oom_splits_and_serial_fallback_completes(settings, batch_novel):
    settings.tts.batch_size = 4
    engine = BatchFixture(fail_batch_over=2)
    manifest = Pipeline(settings, engine).convert(batch_novel)
    assert manifest["status"] == "completed"
    assert len(engine.batches[0]) == 4 and any(len(batch) == 2 for batch in engine.batches)
    serial = BatchFixture(fail_batch_over=1, fingerprint="other-voice")
    assert Pipeline(settings, serial).convert(batch_novel)["status"] == "completed"


def test_only_failed_batch_member_is_retried_with_new_seed(settings, batch_novel, monkeypatch):
    settings.tts.batch_size = 4
    engine = BatchFixture()
    pipe = Pipeline(settings, engine)
    original = pipe._check
    calls = 0

    def fail_first(path, text):
        nonlocal calls
        calls += 1
        qc = original(path, text)
        if calls == 1:
            qc.update({"pass": False, "reasons": ["asr_cer_exceeded"]})
        return qc

    monkeypatch.setattr(pipe, "_check", fail_first)
    manifest = pipe.convert(batch_novel)
    assert manifest["status"] == "completed"
    records = manifest["chapters"][0]["segments"]
    failed_text = records[0]["text"]
    seeds = [seed for text, seed in engine.calls if text == failed_text]
    assert len(seeds) == 2 and seeds[0] != seeds[1]
    assert len(engine.calls) == len(records) + 1


def test_repeated_identical_segments_are_not_sent_twice_in_one_batch(settings, tmp_path):
    settings.tts.batch_size = 4
    settings.segment.target_chars = 100
    settings.segment.max_chars = 120
    source = tmp_path / "repeated.txt"
    same = "她推开窗户，看见雨后的街道。" * 7
    source.write_text("第一章\n" + "\n\n".join([same] * 4), encoding="utf-8")
    engine = BatchFixture()
    result = Pipeline(settings, engine).convert(source)
    assert result["status"] == "completed"
    assert all(len(batch) == len(set(batch)) for batch in engine.batches)


def test_batch_engine_records_actual_rng_and_time_without_gpu(tmp_path):
    import numpy as np
    from local_audiobook.config import Config

    class OfficialAPI:
        def generate_custom_voice(self, **kwargs):
            assert kwargs["text"] == ["第一段", "第二段"] and kwargs["speaker"] == "Serena"
            return [np.ones(2400) * .1, np.ones(4800) * .1], 24000

    engine = QwenEngine.__new__(QwenEngine)
    engine.cfg = Config()
    engine.cfg.tts.device = "cpu"
    engine.model = OfficialAPI()
    paths = [tmp_path / "1.wav", tmp_path / "2.wav"]
    metrics = engine.generate_batch(["第一段", "第二段"], paths, [1, 2])
    assert all(path.is_file() for path in paths)
    assert metrics[0]["batch_seed"] == metrics[1]["batch_seed"]
    assert metrics[0]["batch_id"] == metrics[1]["batch_id"]
    assert sum(m["generation_seconds"] for m in metrics) == metrics[0]["batch_seconds"]
    assert [m["duration"] for m in metrics] == [.1, .2]


def test_corrupt_staged_audio_is_not_accepted(tmp_path):
    record = {"output_path": str(tmp_path / "audio.wav"), "status": "generated", "staged_sha256": "wrong"}
    staged_path(record).write_bytes(b"corrupt")
    assert not staged_valid(record)


def test_stager_skips_adapters_without_batch_support(tmp_path):
    state = State(tmp_path / "state.sqlite3")
    stager = BatchStager(FixtureEngine(), state, 4, "qc", False)
    assert stager.size == 1
    state.close()
