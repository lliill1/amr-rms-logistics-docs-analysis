"""Простой текст, Markdown, CSV."""
from __future__ import annotations

from pathlib import Path

from .base import Block, ParsedDoc, clean, looks_like_heading


def read_text_any(path: Path) -> str:
    data = path.read_bytes()
    for enc in ("utf-8-sig", "cp1251", "koi8-r", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def parse_text(path: Path, **_) -> ParsedDoc:
    raw = read_text_any(path)
    blocks: list[Block] = []
    section = None
    for para in raw.replace("\r", "").split("\n\n"):
        text = clean(para)
        if not text:
            continue
        first = text.split("\n", 1)[0]
        if first.startswith("#") or looks_like_heading(first):
            section = first.lstrip("# ").strip()[:200]
            blocks.append(Block(text=section, kind="heading", page=1, section=section))
            rest = text[len(first):].strip()
            if rest:
                blocks.append(Block(text=rest, kind="text", page=1, section=section))
        else:
            kind = "table" if path.suffix.lower() == ".csv" else "text"
            blocks.append(Block(text=text, kind=kind, page=1, section=section))
    return ParsedDoc(blocks=blocks, pages=1, title=None, needs_ocr=not blocks)
