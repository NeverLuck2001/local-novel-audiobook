import re
from pathlib import Path
import time

from ebooklib import epub
import pytest
from pydantic import ValidationError

from local_audiobook.config import Config, SegmentConfig, TextConfig, load_config
from local_audiobook.parser import parse_book
from local_audiobook.text import clean_text, html_text, segment_text


def compact(text):
    return re.sub(r"\s+", "", text)


@pytest.mark.parametrize("text", [
    "她说：“明天见。路上小心。”随后关上了窗。" * 70,
    "没有标点的特别长对白" * 150,
    "Chapter 1\nDr. Smith paid 3.14 dollars. She said, \"Good morning!\"\n\n下一段。" * 80,
    "第一章\n\n“" + "这是长到必须在引号内部切开的对白，" * 90 + "”\n完。",
    "。？！只有一行。\n\n", "a" * 301,
])
def test_segmentation_preserves_content_and_bounds(text):
    pieces = segment_text(text, SegmentConfig())
    assert compact("".join(s.text for s in pieces)) == compact(text)
    assert all(0 < len(s.text) <= 300 for s in pieces)
    assert all(text[s.start:s.end].strip() == s.text for s in pieces)


def test_quote_kept_whole_when_possible():
    quoted = "她说：“" + "明天我会到河边等你。" * 6 + "”"
    text = "窗外的雨停了。" * 6 + quoted + "门又打开了。" * 10
    pieces = segment_text(text, SegmentConfig(target_chars=100, min_chars=30, max_chars=180))
    assert any(quoted in s.text for s in pieces)


def test_large_novel_segmenting_is_bounded():
    text = ("雨后，她看着窗外的行人，想起了许多年前的约定。\n\n" * 40000)
    started = time.perf_counter()
    pieces = segment_text(text, SegmentConfig())
    assert compact("".join(s.text for s in pieces)) == compact(text)
    assert time.perf_counter() - started < 15


def test_cleaning_preserves_semantics():
    text = "\ufeff她说：‘价钱是3.14元……’\n[123] English https://example.com\x00"
    cleaned, audit = clean_text(text, TextConfig())
    assert "3.14" in cleaned and "……" in cleaned and "[123]" in cleaned
    assert "https://example.com" in cleaned and "English" in cleaned
    assert "\x00" not in cleaned and audit


def test_html_inline_words_are_not_duplicated_or_spaced():
    text = html_text("<body><div><p>她<span>走</span>了。</p><p>第二段<br/>继续。</p></div></body>")
    assert text.count("她走了。") == 1
    assert "第二段\n继续。" in text


@pytest.mark.parametrize("encoding", ["utf-8-sig", "gb18030", "utf-16"])
def test_txt_chapter_patterns_and_encoding(tmp_path, encoding):
    path = tmp_path / "novel.txt"
    path.write_bytes("前言正文。\n第一章 相逢\n正文甲。\n第2章 离别\n正文乙。\nChapter 3 Return\nFinal page.".encode(encoding))
    book = parse_book(path, TextConfig())
    assert len(book.chapters) == 4
    assert book.chapters[1].title == "第一章 相逢"
    assert "正文乙" in book.chapters[2].text


def test_epub_spine_order_short_chapters_cover(tmp_path):
    path = tmp_path / "novel.epub"
    book = epub.EpubBook()
    book.set_identifier("test")
    book.set_title("Test Book")
    book.set_language("zh")
    a = epub.EpubHtml(title="第一章", file_name="a.xhtml", content="<h1>第一章</h1><p>甲。</p>")
    b = epub.EpubHtml(title="第二章", file_name="b.xhtml", content="<h1>第二章</h1><p>乙。</p>")
    book.add_item(b)
    book.add_item(a)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.toc = (a, b)
    book.spine = ["nav", a, b]
    epub.write_epub(str(path), book)
    result = parse_book(path, TextConfig())
    assert [c.title for c in result.chapters] == ["第一章", "第二章"]
    assert any(item["source"] == "nav.xhtml" for item in result.audit)


def test_custom_pattern_and_exclusion_are_audited(tmp_path):
    path = tmp_path / "novel.txt"
    path.write_text("## A\n第一段。\n## B\n广告正文。", encoding="utf-8")
    book = parse_book(path, TextConfig(chapter_pattern=r"^## .+$", exclude_chapter_patterns=[r"^## B$"]))
    assert len(book.chapters) == 1
    assert any("广告正文" in item.get("text", "") for item in book.audit)


def test_config_rejects_typos_and_bad_bounds(tmp_path):
    with pytest.raises(ValidationError):
        Config.model_validate({"qualitty": {"retry": 3}})
    with pytest.raises(ValidationError):
        SegmentConfig(target_chars=400, max_chars=200)
    path = tmp_path / "settings.yaml"
    path.write_text("work_root: work\n", encoding="utf-8")
    assert load_config(path).work_root == tmp_path / "work"


def test_original_fixture_order():
    source = Path(__file__).resolve().parents[1] / "examples/sample.epub"
    book = parse_book(source, TextConfig())
    assert len(book.chapters) == 2
    assert book.cover
    assert "第一章" in book.chapters[0].title


def test_multiple_toc_chapters_inside_one_document(tmp_path):
    path = tmp_path / "shared.epub"
    book = epub.EpubBook()
    book.set_identifier("shared")
    book.set_title("Shared")
    book.set_language("zh")
    chapter = epub.EpubHtml(title="Sections", file_name="all.xhtml", content=
                           '<h1 id="a">起点</h1><p>保留甲正文。</p><h1 id="b">终点</h1><p>保留乙正文。</p>')
    book.add_item(chapter)
    book.toc = (epub.Link("all.xhtml#a", "起点", "a"), epub.Link("all.xhtml#b", "终点", "b"))
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["nav", chapter]
    epub.write_epub(str(path), book)
    parsed = parse_book(path, TextConfig())
    assert [c.title for c in parsed.chapters] == ["起点", "终点"]
    assert "保留甲正文" in parsed.chapters[0].text
    assert "保留乙正文" in parsed.chapters[1].text
