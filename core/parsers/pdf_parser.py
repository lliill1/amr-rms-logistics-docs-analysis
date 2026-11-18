"""PDF: текст постранично (pypdf). Если подключён OCR (core/ocr.py), страницы-сканы и картинки внутри
страниц с текстом тоже превращаются в текст. Без OCR страницы почти без текста помечаются как сканы."""
from __future__ import annotations

import logging
from pathlib import Path

from pypdf import PdfReader

from ..ocr import MIN_IMAGE_TEXT_CHARS
from .base import Block, ParsedDoc, clean, looks_like_heading

logging.getLogger("pypdf").setLevel(logging.ERROR)


def _add_text(blocks: list[Block], text: str, page_no: int, section: str | None) -> str | None:
    """Текстовый слой страницы -> блоки. Абзацы: склеиваем строки, разрывая на заголовках."""
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
    return section


def _add_ocr_paragraphs(blocks: list[Block], paragraphs: list[str], page_no: int, section: str | None) -> str | None:
    """Абзацы, распознанные OCR, -> блоки. Границы абзацев задал Tesseract; заголовки ищем по строкам,
    потому что на сканах заголовок часто слипается с абзацем под ним."""
    for raw in paragraphs:
        buf: list[str] = []

        def flush():
            if buf:
                blocks.append(Block(text=" ".join(buf), kind="text", page=page_no, section=section))
                buf.clear()

        for line in raw.split("\n"):
            line = line.strip()
            if not line:
                continue
            if looks_like_heading(line):
                flush()
                section = line[:200]
                blocks.append(Block(text=line, kind="heading", page=page_no, section=section))
            else:
                buf.append(line)
        flush()
    return section


def parse_pdf(path: Path, min_text_chars_per_page: int = 40, ocr=None, ocr_progress=None, **_) -> ParsedDoc:
    """ocr — движок из core.ocr.get_engine (или None); ocr_progress(сделано, всего) — ход распознавания."""
    reader = PdfReader(str(path))
    blocks: list[Block] = []
    section = None
    empty_pages = 0
    meta_title = None
    try:
        meta_title = (reader.metadata.title or "").strip() if reader.metadata else None
    except Exception:
        meta_title = None

    page_texts: list[str] = []
    for page in reader.pages:
        try:
            raw = page.extract_text() or ""
        except Exception:
            raw = ""
        page_texts.append(clean(raw))

    # OCR: только там, где он нужен — на сканах и (если включено) на картинках внутри страниц
    ocr_by_page: dict[int, list] = {}
    ocr_errors: list[str] = []
    ocr_attempted = False
    if ocr is not None and (ocr.images_in_text_pages or any(len(t) < min_text_chars_per_page for t in page_texts)):
        from ..ocr import ocr_pdf

        ocr_attempted = True
        try:
            items, ocr_errors = ocr_pdf(path, ocr, [len(t) for t in page_texts], min_text_chars_per_page, ocr_progress)
        except Exception as e:  # noqa: BLE001 — не получилось распознать: документ читается как раньше
            items, ocr_errors = [], [f"{type(e).__name__}: {e}"]
        for it in items:
            ocr_by_page.setdefault(it.page, []).append(it)

    ocr_pages: set[int] = set()
    for page_no, text in enumerate(page_texts, start=1):
        items = ocr_by_page.get(page_no, [])
        produced = False
        if len(text) >= min_text_chars_per_page:
            section = _add_text(blocks, text, page_no, section)
            produced = True
        else:
            scan = [p for it in items if it.source == "page" for p in it.paragraphs]
            if sum(len(p) for p in scan) >= min_text_chars_per_page:
                section = _add_ocr_paragraphs(blocks, scan, page_no, section)
                ocr_pages.add(page_no)
                produced = True
        # картинки внутри страницы: после её текста; мелкий шум (подписи кнопок, штампы) не берём
        for it in items:
            if it.source == "image" and sum(len(p) for p in it.paragraphs) >= MIN_IMAGE_TEXT_CHARS:
                section = _add_ocr_paragraphs(blocks, it.paragraphs, page_no, section)
                ocr_pages.add(page_no)
                produced = True
        if not produced:
            empty_pages += 1

    n = len(reader.pages)
    title = meta_title or next((b.text for b in blocks[:10] if 10 <= len(b.text) <= 200), None)
    return ParsedDoc(
        blocks=blocks,
        pages=n,
        title=title,
        needs_ocr=n > 0 and empty_pages / n > 0.5,
        ocr_pages=len(ocr_pages),
        meta={"empty_pages": empty_pages, "ocr_attempted": ocr_attempted, "ocr_errors": ocr_errors[:5]},
    )
