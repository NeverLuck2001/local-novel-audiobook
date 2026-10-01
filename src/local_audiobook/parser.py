"""TXT chapters and EPUB reading order using the mature EbookLib parser."""
from dataclasses import dataclass, field
from pathlib import Path
import re
import uuid
from urllib.parse import unquote

import ebooklib
from ebooklib import epub
from charset_normalizer import from_bytes

from .config import TextConfig
from .text import clean_text, html_text, html_soup


@dataclass
class Chapter:
    title: str
    text: str
    source: str


@dataclass
class Book:
    title: str
    author: str
    chapters: list[Chapter]
    cover: bytes | None = None
    audit: list[dict] = field(default_factory=list)


def _decode(data: bytes, encoding: str | None) -> str:
    if encoding:
        return data.decode(encoding, errors="strict")
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16")
    for candidate in ["utf-8-sig", "gb18030"]:
        try:
            return data.decode(candidate)
        except UnicodeDecodeError:
            pass
    result = from_bytes(data).best()
    if result is None:
        raise ValueError("Cannot detect text encoding; configure text.encoding")
    return str(result)


def _chapters(text: str, title: str, source: str, cfg: TextConfig) -> list[Chapter]:
    pattern = re.compile(cfg.chapter_pattern, re.I)
    found = []
    name, lines = title, []
    for line in text.splitlines():
        if len(line.strip()) <= 100 and pattern.fullmatch(line.strip()):
            if any(part.strip() for part in lines):
                found.append(Chapter(name, "\n".join(lines).strip(), source))
            name, lines = line.strip(), [line.strip()]
        else:
            lines.append(line)
    if any(part.strip() for part in lines):
        found.append(Chapter(name, "\n".join(lines).strip(), source))
    return found


def _cover(eb) -> bytes | None:
    metadata = eb.get_metadata("OPF", "cover")
    for _, attrs in metadata:
        item = eb.get_item_with_id(attrs.get("content", ""))
        if item and item.get_type() in {ebooklib.ITEM_IMAGE, ebooklib.ITEM_COVER}:
            return item.get_content()
    images = list(eb.get_items_of_type(ebooklib.ITEM_IMAGE)) + list(eb.get_items_of_type(ebooklib.ITEM_COVER))
    for item in images:
        if "cover-image" in getattr(item, "properties", []) or "cover" in item.get_name().lower():
            return item.get_content()
    return None


def _document_parts(content: bytes, entries: list[tuple[str, str]], title: str, source: str,
                    cfg: TextConfig, audit: list) -> list[Chapter]:
    """Honor multiple TOC anchor targets inside one spine document."""
    soup = html_soup(content)
    token = "AUDIOBOOK_BOUNDARY_" + uuid.uuid4().hex + "_"
    marked = {}
    seen = set()
    for anchor, section_title in entries:
        if not anchor or anchor in seen:
            continue
        seen.add(anchor)
        node = soup.find(id=anchor)
        if node is None:
            audit.append({"source": source, "operation": "missing_toc_anchor", "anchor": anchor})
            continue
        number = len(marked)
        marked[number] = section_title
        node.insert_before(f"\n{token}{number}_END\n")
    if len(marked) < 2:
        return []
    raw = html_text(str(soup))
    chunks = re.split(re.escape(token) + r"(\d+)_END", raw)
    result = []
    # Preserve a readable preamble before the first TOC section.
    preamble, changes = clean_text(chunks[0], cfg)
    if any(c.isalnum() for c in preamble):
        result.append(Chapter(title, preamble, source))
    audit.extend({"source": source, **change} for change in changes)
    for i in range(1, len(chunks), 2):
        number, raw_text = int(chunks[i]), chunks[i + 1]
        cleaned, changes = clean_text(raw_text, cfg)
        audit.extend({"source": source, **change} for change in changes)
        if any(c.isalnum() for c in cleaned):
            result.append(Chapter(marked[number], cleaned, source))
    audit.append({"source": source, "operation": "split_toc_anchors", "sections": len(result)})
    return result


def parse_book(path: Path, cfg: TextConfig) -> Book:
    if path.suffix.lower() == ".txt":
        text, audit = clean_text(_decode(path.read_bytes(), cfg.encoding), cfg)
        book = Book(path.stem, "", _chapters(text, path.stem, path.name, cfg), audit=audit)
    elif path.suffix.lower() == ".epub":
        eb = epub.read_epub(str(path), options={"ignore_ncx": False})
        def meta(key, default=""):
            values = eb.get_metadata("DC", key)
            return str(values[0][0]) if values else default
        book = Book(meta("title", path.stem), meta("creator"), [], cover=_cover(eb))
        toc_titles = {}
        toc_entries = {}
        def walk_toc(items):
            for item in items:
                if isinstance(item, (tuple, list)):
                    walk_toc(item)
                elif getattr(item, "href", None):
                    href = unquote(item.href).split("#", 1)
                    toc_titles.setdefault(href[0], item.title)
                    toc_entries.setdefault(href[0], []).append((href[1] if len(href) > 1 else "", item.title))
        walk_toc(eb.toc)
        seen = set()
        for item_id, linear in eb.spine:
            item = eb.get_item_with_id(item_id)
            if not item or item.get_type() != ebooklib.ITEM_DOCUMENT:
                continue
            name = item.get_name()
            if item_id in seen:
                book.audit.append({"source": name, "operation": "skip_repeated_spine_item"})
                continue
            seen.add(item_id)
            soup = html_soup(item.get_content())
            has_body_prose = bool(soup.find("p"))
            nav = isinstance(item, epub.EpubNav) or "nav" in getattr(item, "properties", [])
            nav |= bool(re.search(r"(?:^|/)(?:nav|toc|contents)\.(?:xhtml|html|htm)$", name, re.I))
            if nav or linear == "no":
                book.audit.append({"source": name, "operation": "skip_navigation_or_non_linear"})
                continue
            raw = html_text(item.get_content())
            if not any(c.isalnum() for c in raw):
                book.audit.append({"source": name, "operation": "skip_image_only_or_empty"})
                continue
            heading = soup.find(["h1", "h2", "h3"])
            title = heading.get_text(strip=True) if heading else toc_titles.get(name, f"Chapter {len(book.chapters)+1}")
            anchored = _document_parts(item.get_content(), toc_entries.get(name, []), title, name, cfg, book.audit)
            if anchored:
                book.chapters.extend(anchored)
                continue
            cleaned, audit = clean_text(raw, cfg)
            book.audit.extend({"source": name, **record} for record in audit)
            # Only remove adjacent duplicate title lines, never arbitrary body text.
            lines = cleaned.splitlines()
            while len(lines) > 1 and lines[0].strip() == title and lines[1].strip() == title:
                lines.pop(0)
                book.audit.append({"source": name, "operation": "remove_adjacent_duplicate_title"})
            cleaned = "\n".join(lines)
            chapters = _chapters(cleaned, title, name, cfg)
            if len(chapters) > 1 and chapters[0].text == title:
                chapters = chapters[1:]
            book.chapters.extend(chapters)
            if not has_body_prose:
                book.audit.append({"source": name, "operation": "review_non_paragraph_document"})
    else:
        raise ValueError(f"Unsupported file: {path}")
    keep = []
    for chapter in book.chapters:
        if any(re.search(p, chapter.title) for p in cfg.exclude_chapter_patterns):
            book.audit.append({"source": chapter.source, "title": chapter.title,
                               "operation": "user_excluded_chapter", "text": chapter.text})
        elif any(c.isalnum() for c in chapter.text):
            keep.append(chapter)
    book.chapters = keep
    if not book.chapters:
        raise ValueError(f"No readable chapters in {path}")
    return book
