"""Conservative format cleanup and lossless bounded segmentation."""
from dataclasses import dataclass
from bisect import bisect_left, bisect_right
import html
import re
import unicodedata

from bs4 import BeautifulSoup

from .config import TextConfig, SegmentConfig
from .util import digest


def html_soup(content: str | bytes):
    if isinstance(content, bytes):
        content = re.sub(br"<\?xml[^>]*\?>", b"", content)
    else:
        content = re.sub(r"<\?xml[^>]*\?>", "", content)
    return BeautifulSoup(content, "lxml")


def html_text(content: str | bytes) -> str:
    soup = html_soup(content)
    for tag in soup(["script", "style", "head", "nav", "noscript"]):
        tag.decompose()
    for tag in soup.find_all("br"):
        tag.replace_with("\n")
    for tag in soup.find_all(["p", "div", "section", "article", "blockquote", "h1", "h2", "h3",
                              "h4", "h5", "h6", "li", "tr"]):
        tag.insert_before("\n")
        tag.insert_after("\n")
    return (soup.body or soup).get_text()


def clean_text(text: str, cfg: TextConfig) -> tuple[str, list[dict]]:
    audit = []
    original = text
    if re.search(r"</?(?:p|div|br|html|body|span)\b", text, re.I):
        text = html_text(text)
        audit.append({"operation": "strip_html"})
    text = unicodedata.normalize("NFC", html.unescape(text)).replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u00a0", " ").replace("\u3000", " ")
    controls = sum(1 for c in text if unicodedata.category(c) in {"Cc", "Cf", "Cs"} and c not in "\n\t")
    text = "".join(c for c in text if unicodedata.category(c) not in {"Cc", "Cf", "Cs"} or c in "\n\t")
    if controls:
        audit.append({"operation": "remove_controls", "count": controls})
    text = re.sub(r"[ \t]+", " ", text)
    text = "\n".join(line.strip() for line in text.splitlines())
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if cfg.remove_urls:
        # Restrict matching to ASCII URL characters so adjacent Chinese prose
        # remains intact. Bare domains are removed only on domain-only lines.
        url_pattern = re.compile(r"(?:https?://|www\.)[a-z0-9][a-z0-9._~:/?#\[\]@!$&()*+,;=%-]*", re.I)
        urls = []
        def remove_url(match):
            value = match.group().rstrip(".,;!?)")
            urls.append(value)
            return match.group()[len(value):]
        text = url_pattern.sub(remove_url, text)
        bare_domain = re.compile(
            r"(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+(?:com|net|org|cn|cc|io|me|info|xyz|top|site|vip|co|tv)"
            r"(?::\d+)?(?:/[a-z0-9._~:/?#\[\]@!$&()*+,;=%-]*)?", re.I)
        lines = []
        for line in text.splitlines():
            if bare_domain.fullmatch(line.strip()):
                urls.append(line)
            else:
                lines.append(line)
        text = "\n".join(lines)
        if urls:
            audit.append({"operation": "remove_urls", "removed": [value[:500] for value in urls[:100]],
                          "removed_count": len(urls), "removed_sha256": digest(urls),
                          "preview_truncated": len(urls) > 100 or any(len(value) > 500 for value in urls)})
    if cfg.remove_line_patterns:
        patterns = [re.compile(pattern) for pattern in cfg.remove_line_patterns]
        kept, removed = [], []
        for line in text.splitlines():
            matched = next((pattern.pattern for pattern in patterns if pattern.search(line)), None)
            if matched is None:
                kept.append(line)
            else:
                removed.append({"text": line, "pattern": matched})
        text = "\n".join(kept)
        if removed:
            audit.append({"operation": "remove_user_matching_lines",
                          "removed": [{**item, "text": item["text"][:500]} for item in removed[:100]],
                          "removed_count": len(removed), "removed_sha256": digest(removed),
                          "preview_truncated": len(removed) > 100 or any(len(item["text"]) > 500 for item in removed)})
    if cfg.strip_front_matter:
        chapter = re.compile(cfg.chapter_pattern, re.I)
        lines = text.splitlines()
        first = next((i for i, line in enumerate(lines)
                      if len(line.strip()) <= 100 and chapter.fullmatch(line.strip())), None)
        if first is not None and first > 0:
            removed = "\n".join(lines[:first]).strip()
            text = "\n".join(lines[first:])
            if removed:
                audit.append({"operation": "strip_front_matter", "removed": removed[:2000],
                              "removed_chars": len(removed), "removed_sha256": digest(removed),
                              "preview_truncated": len(removed) > 2000,
                              "first_chapter": lines[first]})
    if cfg.join_wrapped_lines:
        text = re.sub(r"(?<![。！？!?…])\n(?!\n)", "", text)
        audit.append({"operation": "join_wrapped_lines"})
    if original != text:
        audit.append({"operation": "format_cleanup", "before_chars": len(original), "after_chars": len(text)})
    return text.strip(), audit


def spoken_text(text: str, replacements: dict[str, str]) -> tuple[str, list[dict]]:
    """Apply explicit, longest-first replacements once; preserve source text."""
    if not replacements:
        return text, []
    pattern = re.compile("|".join(re.escape(key) for key in sorted(replacements, key=len, reverse=True)))
    counts = {}
    def replace(match):
        key = match.group()
        counts[key] = counts.get(key, 0) + 1
        return replacements[key]
    result = pattern.sub(replace, text)
    return result, [{"source": key, "spoken": replacements[key], "count": count}
                    for key, count in counts.items()]


@dataclass(frozen=True)
class Segment:
    text: str
    start: int
    end: int
    boundary: str


def segment_text(text: str, cfg: SegmentConfig) -> list[Segment]:
    """Prefer paragraph/sentence boundaries outside quotes; bound every segment.

    Splits are source slices. Exceptionally long quotations fall back to internal
    punctuation and finally a hard character bound. No content is rewritten.
    """
    if not text.strip():
        return []
    boundaries: dict[int, tuple[str, bool]] = {}
    stack = []
    opening = {"“": "”", "‘": "’", "「": "」", "『": "』", "（": "）", "(": ")"}
    closing = set(opening.values())
    straight_quote = False
    for i, char in enumerate(text):
        if char in opening:
            stack.append(opening[char])
        elif char in closing and stack and stack[-1] == char:
            stack.pop()
        elif char == '"':
            straight_quote = not straight_quote
        outside = not stack and not straight_quote
        kind = None
        if char == "\n":
            kind = "paragraph"
        elif char in "。！？!?；;…":
            kind = "sentence"
        elif char == "." and not (i > 0 and i + 1 < len(text) and text[i - 1].isdigit() and text[i + 1].isdigit()):
            kind = "sentence"
        elif char in "，,、：:":
            kind = "clause"
        elif char in closing | {'"'} and outside and i > 0 and text[i - 1] in "。！？!?…":
            kind = "sentence"
        if kind:
            end = i + 1
            # Attach closing quotes, punctuation and whitespace to the sentence.
            while end < len(text) and text[end] in "”’」』。！？!?…\n \t":
                if text[end] == "\n":
                    kind = "paragraph"
                end += 1
            boundaries[end] = (kind, outside or (end > i + 1 and text[i + 1] in closing))
    boundaries[len(text)] = ("chapter", True)
    offsets = sorted(boundaries)
    segments = []
    start = 0
    while start < len(text):
        while start < len(text) and text[start].isspace():
            start += 1
        if start >= len(text):
            break
        limit = min(len(text), start + cfg.max_chars)
        if limit == len(text):
            end, kind = limit, "chapter"
        else:
            candidates = [(end, *boundaries[end]) for end in offsets[
                bisect_left(offsets, start + cfg.min_chars):bisect_right(offsets, limit)]]
            if candidates:
                def score(candidate, offset=start):
                    end, kind, outside = candidate
                    penalty = {"paragraph": 0, "sentence": 15, "clause": 70, "chapter": 0}[kind]
                    return (0 if outside else 1, abs(end - offset - cfg.target_chars) + penalty)
                end, kind, _ = min(candidates, key=score)
            else:
                # Avoid splitting an English word when a nearby space is available.
                end = limit
                space = text.rfind(" ", start + cfg.min_chars, limit)
                if space >= 0 and space > limit - 30:
                    end = space + 1
                kind = "hard"
        raw = text[start:end]
        if raw.strip():
            segments.append(Segment(raw.strip(), start, end, kind))
        start = end
    def compact(s):
        return re.sub(r"\s+", "", s)
    if compact("".join(s.text for s in segments)) != compact(text):
        raise RuntimeError("Segmentation changed source content")
    return segments
