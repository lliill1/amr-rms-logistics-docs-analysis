"""Word (.docx): абзацы и таблицы в порядке следования, заголовки по стилям, оценка номера страницы."""
from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

from .base import Block, ParsedDoc, clean, looks_like_heading


def _iter_body(doc):
    body = doc.element.body
    for child in body.iterchildren():
        if child.tag == qn("w:p"):
            yield Paragraph(child, doc)
        elif child.tag == qn("w:tbl"):
            yield Table(child, doc)


def _page_breaks(paragraph: Paragraph) -> int:
    """Сколько разрывов страниц внутри абзаца (Word сохраняет lastRenderedPageBreak при сохранении)."""
    el = paragraph._p
    n = len(el.findall(".//" + qn("w:lastRenderedPageBreak")))
    for br in el.findall(".//" + qn("w:br")):
        if br.get(qn("w:type")) == "page":
            n += 1
    return n


def _is_heading(paragraph: Paragraph) -> bool:
    style = (paragraph.style.name if paragraph.style is not None else "") or ""
    s = style.lower()
    if s.startswith(("heading", "заголовок", "title", "название")):
        return True
    return looks_like_heading(paragraph.text)


def _table_text(table: Table) -> str:
    lines = []
    for row in table.rows:
        cells = []
        prev = None
        for cell in row.cells:
            # объединённые ячейки python-docx возвращает повторно — убираем дубли подряд
            if prev is not None and cell._tc is prev:
                continue
            prev = cell._tc
            cells.append(clean(cell.text).replace("\n", " "))
        if any(cells):
            lines.append(" | ".join(cells))
    return "\n".join(lines)


def parse_docx(path: Path, **_) -> ParsedDoc:
    doc = Document(str(path))
    blocks: list[Block] = []
    page = 1
    section = None
    title = (doc.core_properties.title or "").strip() or None

    for item in _iter_body(doc):
        if isinstance(item, Paragraph):
            page += _page_breaks(item)
            text = clean(item.text)
            if not text:
                continue
            if _is_heading(item):
                section = text[:200]
                if title is None and len(text) > 5:
                    title = text[:200]
                blocks.append(Block(text=text, kind="heading", page=page, section=section))
            else:
                blocks.append(Block(text=text, kind="text", page=page, section=section))
        else:
            text = _table_text(item)
            if text:
                blocks.append(Block(text=text, kind="table", page=page, section=section))

    if title is None:
        for b in blocks[:15]:
            if 10 <= len(b.text) <= 200 and b.kind != "table":
                title = b.text
                break

    return ParsedDoc(blocks=blocks, pages=page, title=title, needs_ocr=not blocks)
