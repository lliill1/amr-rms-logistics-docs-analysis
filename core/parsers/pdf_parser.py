"""PDF: текст постранично (pypdf). Страницы почти без текста помечаются как сканы — для них нужен OCR (v3)."""
from __future__ import annotations

import logging
from pathlib import Path

from pypdf import PdfReader

from .base import Block, ParsedDoc, clean, looks_like_heading

logging.getLogger("pypdf").setLevel(logging.ERROR)


def parse_pdf(path: Path, min_text_chars_per_page: int = 40, **_) -> ParsedDoc:
    reader = PdfReader(str(path))
    blocks: list[Block] = []
    section = None
    empty_pages = 0
    meta_title = None
    try:
        meta_title = (reader.metadata.title or "").strip() if reader.metadata else None
    except Exception:
        meta_title = None

    for page_no, page in enumerate(reader.pages, start=1):
        try:
            raw = page.extract_text() or ""
        except Exception:
            raw = ""
        text = clean(raw)
        if len(text) < min_text_chars_per_page:
            empty_pages += 1
            continue
        # абзацы: склеиваем строки, разрывая на заголовках и пустых строках
        para: list[str] = []

        def flush():
            if para:
                blocks.append(Block(text=" ".join(para), kind="text", page=page_no, section=section))
                para.clear()

        for line in text.split("\n"):
            line = line.strip()
            if not line:
                flush()
                continue
            if looks_like_heading(line):
                flush()
                section = line[:200]
                blocks.append(Block(text=line, kind="heading", page=page_no, section=section))
            else:
                para.append(line)
                if line.endswith((".", ":", ";")) and sum(len(p) for p in para) > 400:
                    flush()
        flush()

    n = len(reader.pages)
    title = meta_title or next((b.text for b in blocks[:10] if 10 <= len(b.text) <= 200), None)
    return ParsedDoc(
        blocks=blocks,
        pages=n,
        title=title,
        needs_ocr=n > 0 and empty_pages / n > 0.5,
        meta={"empty_pages": empty_pages},
    )
