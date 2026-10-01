import json
from pathlib import Path
import sys

from local_audiobook.cli import main, discover
from local_audiobook.pipeline import Pipeline
from local_audiobook.config import Config
from test_pipeline import FixtureEngine


def test_case_insensitive_recursive_discovery(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "A.TXT").write_text("正文", encoding="utf-8")
    (tmp_path / "sub/b.EPUB").write_bytes(b"fixture")
    (tmp_path / "ignore.md").write_text("ignored")
    assert len(discover(tmp_path, False)) == 1
    assert len(discover(tmp_path, True)) == 2


def test_invalid_book_does_not_stop_batch(tmp_path, monkeypatch):
    books = tmp_path / "books"
    books.mkdir()
    (books / "a-invalid.epub").write_bytes(b"broken epub")
    (books / "b-good.txt").write_text("第一章 来信\n这是一本正常的小说。" * 5, encoding="utf-8")
    cfg = Config(work_root=tmp_path / "work")
    cfg.output.root = tmp_path / "output"
    cfg.quality.max_chars_per_second = 1000
    cfg.quality.min_chars_per_second = .01
    original_init = Pipeline.__init__

    def with_fixture(self, config):
        original_init(self, config, FixtureEngine())

    monkeypatch.setattr(Pipeline, "__init__", with_fixture)
    monkeypatch.setattr("local_audiobook.cli.load_config", lambda _: cfg)
    result = main([str(books)])
    assert result == 1
    batch = json.loads((tmp_path / "work/batch.json").read_text(encoding="utf-8"))
    assert [item["status"] for item in batch["books"]] == ["failed", "completed"]


def test_workspace_launcher_uses_local_environment():
    import subprocess
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, "main.py", "examples/sample.txt", "--dry-run"],
                            cwd=root, capture_output=True, encoding="utf-8")
    assert result.returncode == 0 and json.loads(result.stdout)["chapters"] == 2
