"""Извлечение требований к AMR/RMS из фрагментов документов.

Главная защита от выдумок модели: у каждого требования должна быть дословная цитата,
и эта цитата проверяется по тексту фрагмента. Не нашлась — требование отбрасывается.
"""
from __future__ import annotations

import re
import sqlite3
from difflib import SequenceMatcher
from typing import Callable

from .config import Settings, load_prompt
from .db import one, rows
from .glossary import is_relevant_fragment

TARGETS = ["AMR", "RMS", "AMR+RMS", "Другое"]
CATEGORIES = ["Функции", "Характеристики", "Интеграция", "Безопасность", "Инфраструктура", "Эксплуатация", "Прочее"]
OBLIGATIONS = ["обязательно", "желательно", "не указано"]

REQUIREMENTS_SCHEMA = {
    "type": "object",
    "properties": {
        "requirements": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "quote": {"type": "string"},
                    "target": {"type": "string", "enum": TARGETS},
                    "category": {"type": "string", "enum": CATEGORIES},
                    "obligation": {"type": "string", "enum": OBLIGATIONS},
                },
                "required": ["text", "quote", "target", "category", "obligation"],
            },
        }
    },
    "required": ["requirements"],
}

_QUOTES = str.maketrans({"«": '"', "»": '"', "“": '"', "”": '"', "„": '"', "’": "'", "‘": "'",
                         "–": "-", "—": "-", "ё": "е", "Ё": "Е", " ": " "})


def normalize(text: str) -> str:
    text = text.translate(_QUOTES).lower()
    text = re.sub(r"\s*\|\s*", " ", text)
    return re.sub(r"\s+", " ", text).strip(" .\"'")


def quote_score(quote: str, source: str) -> float:
    """Доля символов цитаты, найденных в тексте в том же порядке (1.0 — точное вхождение)."""
    q, s = normalize(quote), normalize(source)
    if not q:
        return 0.0
    if q in s:
        return 1.0
    sm = SequenceMatcher(None, s, q, autojunk=False)
    matched = sum(b.size for b in sm.get_matching_blocks() if b.size >= 4)
    return matched / len(q)


def _next_code(conn: sqlite3.Connection) -> str:
    r = one(conn, "SELECT max(CAST(substr(code, 4) AS INTEGER)) AS n FROM requirements WHERE code LIKE 'TR-%'")
    return f"TR-{((r or {}).get('n') or 0) + 1:03d}"


def candidate_chunks(conn: sqlite3.Connection, min_relevance: int = 1, doc_ids: list[int] | None = None,
                     redo: bool = False) -> list[dict]:
    cond = ""
    params: list = [min_relevance]
    if doc_ids:
        cond = f"AND d.id IN ({','.join('?' * len(doc_ids))})"
        params += doc_ids
    if not redo:
        cond += " AND NOT EXISTS (SELECT 1 FROM extraction_log l WHERE l.chunk_id = c.id AND l.kind = 'requirements')"
    chunks = rows(conn, f"""SELECT c.id, c.doc_id, c.text, c.section, c.page_from, d.title, d.rel_path
                            FROM chunks c JOIN documents d ON d.id = c.doc_id
                            WHERE d.is_current = 1 AND d.relevance >= ? {cond}
                            ORDER BY d.relevance DESC, c.doc_id, c.seq""", params)
    return [c for c in chunks if is_relevant_fragment(c["text"])]


def extract_requirements(conn: sqlite3.Connection, settings: Settings, llm, doc_ids: list[int] | None = None,
                         redo: bool = False, progress: Callable[[int, int], None] | None = None,
                         stop: Callable[[], bool] | None = None) -> dict:
    threshold = float(settings.get("extraction.quote_match_threshold", 0.9))
    chunks = candidate_chunks(conn, int(settings.get("extraction.min_relevance", 1)), doc_ids, redo)
    template = load_prompt("requirements")
    stats = {"chunks": len(chunks), "found": 0, "accepted": 0, "rejected_quotes": 0, "errors": 0}

    for i, c in enumerate(chunks, start=1):
        if stop and stop():
            break
        if progress:
            progress(i, len(chunks))
        prompt = template.format(title=c["title"] or c["rel_path"], page=c["page_from"] or "?", text=c["text"])
        try:
            data = llm.chat_json(prompt, REQUIREMENTS_SCHEMA)
            items = data.get("requirements", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
        except Exception as e:  # noqa: BLE001
            stats["errors"] += 1
            conn.execute("INSERT OR REPLACE INTO extraction_log(chunk_id, kind, found, error) VALUES (?, 'requirements', 0, ?)",
                         (c["id"], str(e)[:300]))
            conn.commit()
            continue

        accepted = 0
        for it in items:
            if not isinstance(it, dict) or not it.get("text"):
                continue
            stats["found"] += 1
            score = quote_score(it.get("quote", ""), c["text"])
            if score < threshold:
                stats["rejected_quotes"] += 1
                continue
            dup = one(conn, "SELECT id FROM requirements WHERE doc_id = ? AND lower(quote) = lower(?)",
                      (c["doc_id"], it.get("quote", "")))
            if dup:
                continue
            conn.execute(
                """INSERT INTO requirements(code, doc_id, chunk_id, page, text, target, category, obligation, quote, quote_score)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (_next_code(conn), c["doc_id"], c["id"], c["page_from"], it["text"].strip(),
                 it.get("target") if it.get("target") in TARGETS else "Другое",
                 it.get("category") if it.get("category") in CATEGORIES else "Прочее",
                 it.get("obligation") if it.get("obligation") in OBLIGATIONS else "не указано",
                 it.get("quote", "").strip(), round(score, 3)))
            accepted += 1
        stats["accepted"] += accepted
        conn.execute("INSERT OR REPLACE INTO extraction_log(chunk_id, kind, found, error) VALUES (?, 'requirements', ?, NULL)",
                     (c["id"], accepted))
        conn.commit()
    return stats
