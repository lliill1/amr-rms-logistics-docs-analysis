"""Парсеры документов. Каждый возвращает ParsedDoc: список блоков с номерами страниц и разделами."""
from __future__ import annotations

from pathlib import Path

from .base import Block, ParsedDoc, UnsupportedFormat
from .docx_parser import parse_docx
from .eml_parser import parse_eml
from .pdf_parser import parse_pdf
from .text_parser import parse_text
from .xlsx_parser import parse_xlsx

PARSERS = {
    ".docx": parse_docx,
    ".docm": parse_docx,
    ".pdf": parse_pdf,
    ".xlsx": parse_xlsx,
    ".xlsm": parse_xlsx,
    ".txt": parse_text,
    ".md": parse_text,
    ".csv": parse_text,
    ".eml": parse_eml,
}

SUPPORTED_EXTS = set(PARSERS)


def parse_file(path: Path, **kwargs) -> ParsedDoc:
    parser = PARSERS.get(path.suffix.lower())
    if parser is None:
        raise UnsupportedFormat(path.suffix)
    return parser(path, **kwargs)


__all__ = ["Block", "ParsedDoc", "UnsupportedFormat", "parse_file", "SUPPORTED_EXTS"]
