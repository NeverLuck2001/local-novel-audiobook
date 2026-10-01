"""Build an original Chinese fixture with a cover and intentionally shuffled manifest order."""
import html
import io
from pathlib import Path

from ebooklib import epub
from PIL import Image, ImageDraw


def main():
    root = Path(__file__).resolve().parents[1]
    parts = (root / "examples/sample.txt").read_text(encoding="utf-8").split("第二章 河边的小屋")
    book = epub.EpubBook()
    book.set_identifier("local-novel-audiobook-original-fixture-v1")
    book.set_title("雨后的来信")
    book.set_language("zh")
    book.add_author("本地管线测试")
    image = Image.new("RGB", (600, 800), "#e6ede8")
    draw = ImageDraw.Draw(image)
    draw.rectangle((80, 100, 520, 640), fill="#315a53")
    draw.line((100, 610, 500, 300), fill="#c6d4c3", width=10)
    draw.text((100, 690), "AFTER THE RAIN", fill="#315a53", font_size=36)
    cover = io.BytesIO()
    image.save(cover, "JPEG")
    book.set_cover("cover.jpg", cover.getvalue())
    chapters = []
    for i, (title, text) in enumerate([("第一章 雨后的来信", parts[0].split("\n", 1)[1]),
                                     ("第二章 河边的小屋", parts[1])], 1):
        chapter = epub.EpubHtml(title=title, file_name=f"chapter{i}.xhtml", lang="zh")
        chapter.content = "<h1>" + html.escape(title) + "</h1>" + "".join(
            "<p>" + html.escape(paragraph.strip()) + "</p>" for paragraph in text.split("\n\n") if paragraph.strip())
        chapters.append(chapter)
    for chapter in reversed(chapters):
        book.add_item(chapter)
    book.toc = tuple(chapters)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["nav", *chapters]
    epub.write_epub(str(root / "examples/sample.epub"), book)


if __name__ == "__main__":
    main()
