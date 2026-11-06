"""Извлечение фактов из фрагментов документов (версия 2).

Виды фактов:
    requirements — требования к AMR/RMS (как в версии 1);
    processes    — логистические процессы с участием роботов: триггер, инициатор, шаги, откуда/куда;
    integration  — обмены между системами и границы ответственности «функция → система».

Для каждого факта модель обязана привести дословную цитату; цитата проверяется по тексту фрагмента,
и факт без подтверждённой цитаты отбрасывается. Каждый фрагмент обрабатывается каждым видом один раз
(журнал extraction_log), поэтому извлечение можно останавливать и продолжать.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Callable

from .config import Settings, load_prompt
from .db import one, rows
from .extract import REQUIREMENTS_SCHEMA, normalize, quote_score, save_requirements
from .glossary import is_relevant_fragment
from .routes import guess_route
from .vocab import Vocab, load_vocab

STATUSES = ("auto", "confirmed", "edited", "rejected")
ACTIVE = ("auto", "confirmed", "edited")          # не отклонённые


# ───────── схемы ответа модели (JSON Schema для Ollama format) ─────────

def processes_schema(v: Vocab) -> dict:
    return {
        "type": "object",
        "properties": {"processes": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "trigger": {"type": "string"},
                "initiator": {"type": "string", "enum": v.systems},
                "amr_role": {"type": "string", "enum": v.amr_roles},
                "from_location": {"type": "string"},
                "to_location": {"type": "string"},
                "cargo": {"type": "string"},
                "steps": {"type": "array", "items": {
                    "type": "object",
                    "properties": {"actor": {"type": "string", "enum": v.systems}, "action": {"type": "string"}},
                    "required": ["actor", "action"]}},
                "quote": {"type": "string"},
            },
            "required": ["name", "initiator", "amr_role", "from_location", "to_location", "steps", "quote"],
        }}},
        "required": ["processes"],
    }


def integration_schema(v: Vocab) -> dict:
    return {
        "type": "object",
        "properties": {
            "exchanges": {"type": "array", "items": {
                "type": "object",
                "properties": {
                    "source": {"type": "string", "enum": v.systems},
                    "target": {"type": "string", "enum": v.systems},
                    "message": {"type": "string"},
                    "protocol": {"type": "string"},
                    "trigger": {"type": "string"},
                    "quote": {"type": "string"},
                },
                "required": ["source", "target", "message", "quote"]}},
            "responsibilities": {"type": "array", "items": {
                "type": "object",
                "properties": {
                    "function": {"type": "string", "enum": v.function_titles},
                    "system": {"type": "string", "enum": v.systems},
                    "quote": {"type": "string"},
                },
                "required": ["function", "system", "quote"]}},
        },
        "required": ["exchanges", "responsibilities"],
    }


# ───────── сохранение ─────────

def next_code(conn: sqlite3.Connection, table: str, prefix: str) -> str:
    r = one(conn, f"SELECT max(CAST(substr(code, {len(prefix) + 1}) AS INTEGER)) AS n FROM {table} WHERE code LIKE ?",
            (f"{prefix}%",))
    return f"{prefix}{((r or {}).get('n') or 0) + 1:03d}"


def _s(value) -> str:
    return str(value or "").strip()


def _checked(it: dict, chunk: dict, threshold: float, st: dict) -> float | None:
    """Счётчики + проверка цитаты. None — факт отброшен."""
    st["found"] += 1
    score = quote_score(_s(it.get("quote")), chunk["text"])
    if score < threshold:
        st["rejected_quotes"] += 1
        return None
    return round(score, 3)


def save_processes(conn: sqlite3.Connection, chunk: dict, data, threshold: float, v: Vocab) -> dict:
    st = {"found": 0, "accepted": 0, "rejected_quotes": 0}
    items = data.get("processes", []) if isinstance(data, dict) else []
    for it in items:
        if not isinstance(it, dict) or not _s(it.get("name")):
            continue
        score = _checked(it, chunk, threshold, st)
        if score is None:
            continue
        name = _s(it.get("name"))[:200]
        if one(conn, "SELECT id FROM processes WHERE doc_id = ? AND lower(name) = lower(?)", (chunk["doc_id"], name)):
            continue
        src, dst = _s(it.get("from_location")), _s(it.get("to_location"))
        if not src and not dst:            # маленькие модели часто не заполняют маршрут — восстанавливаем по тексту
            src, dst = (x or "" for x in guess_route(name, _s(it.get("quote"))))
        steps = []
        for s in it.get("steps") or []:
            if isinstance(s, dict) and _s(s.get("action")):
                steps.append({"actor": v.system(s.get("actor")) or "Другое", "action": _s(s.get("action"))[:300]})
        conn.execute(
            """INSERT INTO processes(code, doc_id, chunk_id, page, name, trigger, initiator, amr_role, from_location,
                                     to_location, cargo, steps, quote, quote_score)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (next_code(conn, "processes", "PR-"), chunk["doc_id"], chunk["id"], chunk["page_from"], name,
             _s(it.get("trigger")) or None, v.system(it.get("initiator")), v.amr_role(it.get("amr_role")),
             src or None, dst or None, _s(it.get("cargo")) or None,
             json.dumps(steps, ensure_ascii=False), _s(it.get("quote")), score))
        st["accepted"] += 1
    return st


def save_integration(conn: sqlite3.Connection, chunk: dict, data, threshold: float, v: Vocab) -> dict:
    st = {"found": 0, "accepted": 0, "rejected_quotes": 0}
    if not isinstance(data, dict):
        return st
    for it in data.get("exchanges") or []:
        if not isinstance(it, dict) or not _s(it.get("message")):
            continue
        src, dst = v.system(it.get("source")), v.system(it.get("target"))
        if not src or not dst or src == dst:
            continue
        score = _checked(it, chunk, threshold, st)
        if score is None:
            continue
        message = _s(it.get("message"))[:200]
        if one(conn, """SELECT id FROM interfaces WHERE doc_id = ? AND source_system = ? AND target_system = ?
                        AND lower(message) = lower(?)""", (chunk["doc_id"], src, dst, message)):
            continue
        conn.execute(
            """INSERT INTO interfaces(code, doc_id, chunk_id, page, source_system, target_system, message, protocol,
                                      trigger, quote, quote_score) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (next_code(conn, "interfaces", "IF-"), chunk["doc_id"], chunk["id"], chunk["page_from"], src, dst, message,
             _s(it.get("protocol")) or None, _s(it.get("trigger")) or None, _s(it.get("quote")), score))
        st["accepted"] += 1
    for it in data.get("responsibilities") or []:
        if not isinstance(it, dict):
            continue
        fkey, system = v.function_key(it.get("function")), v.system(it.get("system"))
        if not fkey or not system:
            continue
        score = _checked(it, chunk, threshold, st)
        if score is None:
            continue
        if one(conn, "SELECT id FROM responsibilities WHERE doc_id = ? AND function = ? AND system = ?",
               (chunk["doc_id"], fkey, system)):
            continue
        conn.execute(
            """INSERT INTO responsibilities(code, doc_id, chunk_id, page, function, system, quote, quote_score)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (next_code(conn, "responsibilities", "RS-"), chunk["doc_id"], chunk["id"], chunk["page_from"], fkey, system,
             _s(it.get("quote")), score))
        st["accepted"] += 1
    return st


# ───────── виды фактов ─────────

@dataclass(frozen=True)
class Kind:
    key: str
    title: str
    prompt: str
    schema: Callable[[Vocab], dict]
    save: Callable[[sqlite3.Connection, dict, object, float, Vocab], dict]


KINDS: dict[str, Kind] = {
    "requirements": Kind("requirements", "Требования", "requirements", lambda v: REQUIREMENTS_SCHEMA,
                         lambda conn, c, data, thr, v: save_requirements(
                             conn, c, data.get("requirements", []) if isinstance(data, dict)
                             else (data if isinstance(data, list) else []), thr)),
    "processes": Kind("processes", "Процессы", "processes", processes_schema, save_processes),
    "integration": Kind("integration", "Обмены и ответственность", "integration", integration_schema, save_integration),
}
ALL_KINDS = list(KINDS)


def fill_missing_routes(conn: sqlite3.Connection) -> int:
    """Процессам без «откуда» и «куда» восстанавливает маршрут по названию и цитате.
    Ничего не трогает, если хотя бы одно из полей заполнено (в том числе аналитиком)."""
    n = 0
    for p in rows(conn, """SELECT id, name, quote FROM processes
                           WHERE coalesce(trim(from_location), '') = '' AND coalesce(trim(to_location), '') = ''"""):
        src, dst = guess_route(p["name"], p["quote"])
        if src and dst:
            conn.execute("UPDATE processes SET from_location = ?, to_location = ? WHERE id = ?", (src, dst, p["id"]))
            n += 1
    if n:
        conn.commit()
    return n


def pending_chunks(conn: sqlite3.Connection, kinds: list[str] | None = None, min_relevance: int = 1,
                   doc_ids: list[int] | None = None, redo: bool = False) -> list[dict]:
    """Фрагменты актуальных документов про AMR/RMS, ещё не обработанные хотя бы одним из видов.
    У каждого фрагмента поле kinds — какие виды для него ещё не выполнены."""
    kinds = [k for k in (kinds or ALL_KINDS) if k in KINDS]
    if not kinds:
        return []
    params: list = [min_relevance]
    cond = ""
    if doc_ids:
        cond = f"AND d.id IN ({','.join('?' * len(doc_ids))})"
        params += doc_ids
    chunks = rows(conn, f"""SELECT c.id, c.doc_id, c.text, c.section, c.page_from, d.title, d.rel_path
                            FROM chunks c JOIN documents d ON d.id = c.doc_id
                            WHERE d.is_current = 1 AND d.relevance >= ? {cond}
                            ORDER BY d.relevance DESC, c.doc_id, c.seq""", params)
    done: dict[int, set[str]] = {}
    if not redo:
        for r in rows(conn, "SELECT chunk_id, kind FROM extraction_log"):
            done.setdefault(r["chunk_id"], set()).add(r["kind"])
    out = []
    for c in chunks:
        missing = [k for k in kinds if k not in done.get(c["id"], set())]
        if missing and is_relevant_fragment(c["text"]):
            c["kinds"] = missing
            out.append(c)
    return out


def pending_counts(conn: sqlite3.Connection, min_relevance: int = 1) -> dict[str, int]:
    chunks = pending_chunks(conn, ALL_KINDS, min_relevance)
    return {k: sum(k in c["kinds"] for c in chunks) for k in ALL_KINDS}


def _prompt(kind: Kind, chunk: dict, v: Vocab) -> str:
    return load_prompt(kind.prompt).format(
        title=chunk["title"] or chunk["rel_path"], page=chunk["page_from"] or "?", text=chunk["text"],
        systems=", ".join(v.systems), functions="; ".join(v.function_titles), roles=", ".join(v.amr_roles))


def run_extraction(conn: sqlite3.Connection, settings: Settings, llm, kinds: list[str] | None = None,
                   doc_ids: list[int] | None = None, redo: bool = False, limit: int | None = None,
                   progress: Callable[[int, int], None] | None = None,
                   stop: Callable[[], bool] | None = None) -> dict:
    """Обрабатывает фрагменты выбранными видами. limit — сколько фрагментов взять за запуск."""
    v = load_vocab()
    threshold = float(settings.get("extraction.quote_match_threshold", 0.9))
    kinds = [k for k in (kinds or ALL_KINDS) if k in KINDS]
    chunks = pending_chunks(conn, kinds, int(settings.get("extraction.min_relevance", 1)), doc_ids, redo)
    if limit:
        chunks = chunks[:limit]
    total = {"chunks": len(chunks), "found": 0, "accepted": 0, "rejected_quotes": 0, "errors": 0}
    by_kind = {k: {"found": 0, "accepted": 0, "rejected_quotes": 0, "errors": 0} for k in kinds}

    for i, c in enumerate(chunks, start=1):
        if stop and stop():
            break
        if progress:
            progress(i, len(chunks))
        for key in c["kinds"]:
            kind = KINDS[key]
            try:
                data = llm.chat_json(_prompt(kind, c, v), kind.schema(v))
            except Exception as e:  # noqa: BLE001 — один сбой модели не должен останавливать весь архив
                by_kind[key]["errors"] += 1
                total["errors"] += 1
                conn.execute("INSERT OR REPLACE INTO extraction_log(chunk_id, kind, found, error) VALUES (?, ?, 0, ?)",
                             (c["id"], key, str(e)[:300]))
                conn.commit()
                continue
            st = kind.save(conn, c, data, threshold, v)
            for k2 in ("found", "accepted", "rejected_quotes"):
                by_kind[key][k2] += st[k2]
                total[k2] += st[k2]
            conn.execute("INSERT OR REPLACE INTO extraction_log(chunk_id, kind, found, error) VALUES (?, ?, ?, NULL)",
                         (c["id"], key, st["accepted"]))
            conn.commit()
    total["by_kind"] = by_kind
    total["routes_filled"] = fill_missing_routes(conn)
    return total


__all__ = ["KINDS", "ALL_KINDS", "ACTIVE", "STATUSES", "pending_chunks", "pending_counts", "run_extraction", "fill_missing_routes",
           "next_code", "normalize"]
