from __future__ import annotations

import re
from dataclasses import dataclass, field


class UnsupportedFormat(Exception):
    pass


@dataclass
class Block:
    text: str
    kind: str = "text"          # heading | text | table
    page: int | None = None
    section: str | None = None


@dataclass
class ParsedDoc:
    blocks: list[Block] = field(default_factory=list)
    pages: int | None = None
    title: str | None = None
    needs_ocr: bool = False      # текста почти нет — вероятно, скан
    meta: dict = field(default_factory=dict)

    @property
    def full_text(self) -> str:
        return "\n".join(b.text for b in self.blocks)

    @property
    def head_text(self) -> str:
        """Начало документа (титул, лист утверждения) — для определения карточки."""
        out, size = [], 0
        for b in self.blocks:
            out.append(b.text)
            size += len(b.text)
            if size > 4000:
                break
        return "\n".join(out)


_WS = re.compile(r"[ \t ]+")


def clean(text: str) -> str:
    text = text.replace("\r", "\n")
    text = _WS.sub(" ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


# «1.2.3 Название раздела» или «Раздел 2. …»
NUMBERED_HEADING = re.compile(r"^(\d+(\.\d+){0,4}\.?|раздел\s+\d+\.?)\s+[A-ZА-ЯЁ][^\n]{2,120}$", re.IGNORECASE)


def looks_like_heading(line: str) -> bool:
    """Нумерованный заголовок: короткая строка без точки в конце («3.2 Транспортные задания»).
    Пункты списка вида «5.1 Робот должен ... .» заголовками не считаются."""
    line = line.strip()
    if not line or len(line) > 110 or len(line.split()) > 12:
        return False
    if line.endswith((".", ",", ";", ":")):
        return False
    return bool(NUMBERED_HEADING.match(line))
