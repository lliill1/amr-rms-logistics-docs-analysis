"""Вопросы к архиву: поиск фрагментов и ответ модели строго по ним, со ссылками [n]."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from .config import Settings, load_prompt
from .index import Hit, search


@dataclass
class Answer:
    text: str | None          # None — модель недоступна, показываем только фрагменты
    hits: list[Hit]
    error: str | None = None


def source_label(h: Hit) -> str:
    pages = ""
    if h.page_from:
        pages = f", с. {h.page_from}" if h.page_from == h.page_to or not h.page_to else f", с. {h.page_from}–{h.page_to}"
    return f"{h.rel_path}{pages}"


def format_fragments(hits: list[Hit], max_chars: int = 1800) -> str:
    parts = []
    for n, h in enumerate(hits, start=1):
        flags = []
        if not h.is_current:
            flags.append("устаревшая редакция")
        if h.approval and h.approval != "не указано":
            flags.append(h.approval)
        if h.side and h.side != "не указано":
            flags.append(h.side)
        meta = f" ({'; '.join(flags)})" if flags else ""
        section = f"\nРаздел: {h.section}" if h.section else ""
        parts.append(f"[{n}] {source_label(h)}{meta}{section}\n{h.text[:max_chars]}")
    return "\n\n".join(parts)


def ask(conn: sqlite3.Connection, settings: Settings, question: str, llm=None, only_current: bool = True) -> Answer:
    hits = search(conn, question, llm=llm, top_k=int(settings.get("search.top_k", 6)),
                  only_current=only_current, use_vectors=bool(settings.get("search.use_vectors", True)))
    if not hits:
        return Answer(text="В архиве не нашлось подходящих фрагментов. Попробуйте переформулировать вопрос.", hits=[])
    if llm is None:
        return Answer(text=None, hits=hits)
    prompt = load_prompt("answer").format(question=question, fragments=format_fragments(hits))
    try:
        return Answer(text=llm.chat(prompt), hits=hits)
    except Exception as e:  # noqa: BLE001
        return Answer(text=None, hits=hits, error=str(e))
