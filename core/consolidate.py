"""Сведение фактов (версия 2): матрица ответственности «функция × система», противоречия и пробелы.

Правила (все детерминированные, без модели — их легко проверить и объяснить заказчику):

* противоречие по ответственности — за одну функцию в АКТУАЛЬНЫХ документах названы разные системы;
* изменение между редакциями — в актуальных документах одна система, а в устаревших редакциях другая;
* пробел по ответственности — для обязательной функции (config/model.yaml, required: true) нет ни одного факта;
* противоречие по протоколу — для одной пары систем в актуальных документах указаны разные протоколы;
* пробел по обмену — нет ни одного обмена из списка required_links (или не указан протокол, если он обязателен).

Решения аналитика (принят вариант, «не противоречие», вынесено в вопросы) хранятся в таблице conflicts
по стабильному ключу и переживают пересчёт. Если набор фактов за противоречием изменился (появился новый
источник), решение сбрасывается и противоречие снова открыто.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field

from .db import one, rows
from .facts import ACTIVE
from .vocab import Vocab, load_vocab

KIND_LABELS = {"contradiction": "Противоречие", "revision": "Изменение между редакциями", "gap": "Пробел"}
STATUS_LABELS = {"open": "открыто", "resolved": "решено", "dismissed": "не противоречие", "question": "вопрос заказчику"}

_EVIDENCE_SQL = """SELECT r.id, r.code, r.function, r.system, r.status, r.page, r.quote,
                          d.id AS doc_id, d.rel_path, d.doc_date, d.approval, d.side, d.is_current
                   FROM responsibilities r JOIN documents d ON d.id = r.doc_id"""
_IF_SQL = """SELECT i.id, i.code, i.source_system, i.target_system, i.message, i.protocol, i.status, i.page, i.quote,
                    d.id AS doc_id, d.rel_path, d.doc_date, d.approval, d.side, d.is_current
             FROM interfaces i JOIN documents d ON d.id = i.doc_id"""


def _statuses(include_auto: bool) -> tuple[str, ...]:
    return ACTIVE if include_auto else ("confirmed", "edited")


def _in(statuses: tuple[str, ...]) -> str:
    return ",".join(f"'{s}'" for s in statuses)


def source_label(e: dict) -> str:
    return e["rel_path"] + (f", с. {int(e['page'])}" if e.get("page") is not None else "")


def _src(e: dict) -> dict:
    return {"code": e["code"], "status": e["status"], "rel_path": e["rel_path"], "page": e.get("page"),
            "label": source_label(e), "quote": e.get("quote"), "doc_date": e.get("doc_date"),
            "approval": e.get("approval"), "side": e.get("side"), "is_current": bool(e["is_current"])}


# ───────── матрица ответственности ─────────

def responsibility_matrix(conn: sqlite3.Connection, include_auto: bool = True, vocab: Vocab | None = None) -> dict:
    v = vocab or load_vocab()
    ev = rows(conn, _EVIDENCE_SQL + f" WHERE r.status IN ({_in(_statuses(include_auto))}) ORDER BY r.code")
    used = {e["system"] for e in ev}
    systems = [s for s in v.systems if s in used or s in ("SAP EWM", "MES", "ERP", "RMS", "AMR")]
    out_rows = []
    for f in v.functions:
        cells: dict[str, list] = {}
        for e in ev:
            if e["function"] == f.key:
                cells.setdefault(e["system"], []).append(_src(e))
        current = sorted({s for s, lst in cells.items() if any(x["is_current"] for x in lst)})
        state = "conflict" if len(current) > 1 else ("ok" if current else ("outdated" if cells else ("gap" if f.required else "empty")))
        out_rows.append({"key": f.key, "title": f.title, "required": f.required, "state": state,
                         "systems": current, "cells": cells})
    return {"systems": systems, "rows": out_rows, "include_auto": include_auto}


# ───────── поиск противоречий и пробелов ─────────

@dataclass
class Draft:
    key: str
    kind: str
    title: str
    details: dict = field(default_factory=dict)
    signature: str = ""


def detect(conn: sqlite3.Connection, vocab: Vocab | None = None) -> list[Draft]:
    v = vocab or load_vocab()
    drafts: list[Draft] = []

    # ответственность
    ev = rows(conn, _EVIDENCE_SQL + f" WHERE r.status IN ({_in(ACTIVE)}) ORDER BY r.code")
    for f in v.functions:
        fe = [e for e in ev if e["function"] == f.key]
        cur = sorted({e["system"] for e in fe if e["is_current"]}, key=v.systems.index)
        old = sorted({e["system"] for e in fe if not e["is_current"]} - set(cur), key=v.systems.index)
        variants = [{"value": s, "current": s in cur, "sources": [_src(e) for e in fe if e["system"] == s]}
                    for s in cur + old]
        sig = ",".join(sorted(e["code"] for e in fe))
        if len(cur) > 1:
            drafts.append(Draft(f"resp:{f.key}", "contradiction", f"Кто отвечает за «{f.title}»: {' или '.join(cur)}?",
                                {"area": "responsibility", "function": f.key, "function_title": f.title, "variants": variants}, sig))
        elif len(cur) == 1 and old:
            drafts.append(Draft(f"rev:{f.key}", "revision",
                                f"«{f.title}»: в ранних редакциях — {', '.join(old)}, в актуальных — {cur[0]}",
                                {"area": "responsibility", "function": f.key, "function_title": f.title, "variants": variants}, sig))
        elif not fe and f.required:
            drafts.append(Draft(f"gap:resp:{f.key}", "gap", f"Не определено, какая система отвечает за «{f.title}»",
                                {"area": "responsibility", "function": f.key, "function_title": f.title, "variants": []}, "none"))

    # обмены: противоречия по протоколу и обязательные обмены
    ifs = rows(conn, _IF_SQL + f" WHERE i.status IN ({_in(ACTIVE)}) ORDER BY i.code")
    pairs: dict[tuple[str, str], list[dict]] = {}
    for i in ifs:
        pairs.setdefault(tuple(sorted((i["source_system"], i["target_system"]))), []).append(i)
    for (a, b), lst in pairs.items():
        cur = [i for i in lst if i["is_current"] and (i["protocol"] or "").strip()]
        protos = sorted({i["protocol"].strip() for i in cur}, key=str.lower)
        if len({p.lower() for p in protos}) > 1:
            variants = [{"value": p, "current": True, "sources": [_src(i) for i in cur if i["protocol"].strip() == p]} for p in protos]
            drafts.append(Draft(f"proto:{a}|{b}", "contradiction", f"Протокол обмена {a} ↔ {b}: {' или '.join(protos)}?",
                                {"area": "protocol", "pair": [a, b], "variants": variants},
                                ",".join(sorted(i["code"] for i in cur))))
    for link in v.required_links:
        a, bs, title = link.get("a"), set(link.get("b") or []), link.get("title", "")
        found = [i for i in ifs if (i["source_system"] == a and i["target_system"] in bs)
                 or (i["target_system"] == a and i["source_system"] in bs)]
        lkey = f"{a}:{'+'.join(sorted(bs))}"
        if not found:
            drafts.append(Draft(f"gap:link:{lkey}", "gap", f"Не описан обмен: {title}",
                                {"area": "link", "title": title, "a": a, "b": sorted(bs), "variants": []}, "none"))
        elif link.get("need_protocol") and not any((i["protocol"] or "").strip() for i in found):
            drafts.append(Draft(f"gap:proto:{lkey}", "gap", f"Не указан протокол: {title}",
                                {"area": "link", "title": title, "a": a, "b": sorted(bs), "variants": [],
                                 "sources": [_src(i) for i in found]}, ",".join(sorted(i["code"] for i in found))))
    return drafts


def refresh(conn: sqlite3.Connection, vocab: Vocab | None = None) -> list[dict]:
    """Пересчитывает противоречия и сохраняет их, не теряя решений аналитика."""
    drafts = detect(conn, vocab)
    keys = {d.key for d in drafts}
    existing = {r["key"]: r for r in rows(conn, "SELECT * FROM conflicts")}
    for d in drafts:
        details = json.dumps(d.details, ensure_ascii=False)
        old = existing.get(d.key)
        if old is None:
            conn.execute("INSERT INTO conflicts(key, kind, title, details, signature) VALUES (?, ?, ?, ?, ?)",
                         (d.key, d.kind, d.title, details, d.signature))
        elif old["signature"] != d.signature and old["status"] in ("resolved", "dismissed"):
            # появились новые факты — решение нужно пересмотреть
            conn.execute("""UPDATE conflicts SET kind = ?, title = ?, details = ?, signature = ?, status = 'open',
                            resolution = NULL, updated_at = datetime('now') WHERE id = ?""",
                         (d.kind, d.title, details, d.signature, old["id"]))
        else:
            conn.execute("UPDATE conflicts SET kind = ?, title = ?, details = ?, signature = ? WHERE id = ?",
                         (d.kind, d.title, details, d.signature, old["id"]))
    # открытые и больше не обнаруживаемые — убираем; решённые остаются в истории
    for key, old in existing.items():
        if key not in keys and old["status"] == "open":
            conn.execute("DELETE FROM conflicts WHERE id = ?", (old["id"],))
    conn.commit()
    return list_conflicts(conn)


def conflict_out(r: dict) -> dict:
    r = dict(r)
    r["details"] = json.loads(r["details"] or "{}")
    r["kind_label"] = KIND_LABELS.get(r["kind"], r["kind"])
    r["status_label"] = STATUS_LABELS.get(r["status"], r["status"])
    return r


def list_conflicts(conn: sqlite3.Connection) -> list[dict]:
    order = "CASE kind WHEN 'contradiction' THEN 0 WHEN 'revision' THEN 1 ELSE 2 END"
    return [conflict_out(r) for r in rows(conn, f"SELECT * FROM conflicts ORDER BY CASE status WHEN 'open' THEN 0 ELSE 1 END, {order}, id")]


def get_conflict(conn: sqlite3.Connection, conflict_id: int) -> dict | None:
    r = one(conn, "SELECT * FROM conflicts WHERE id = ?", (conflict_id,))
    return conflict_out(r) if r else None


# ───────── решения аналитика ─────────

def resolve(conn: sqlite3.Connection, conflict_id: int, value: str) -> dict:
    """Принять вариант. Для ответственности: факты с выбранной системой подтверждаются, остальные отклоняются."""
    c = get_conflict(conn, conflict_id)
    if c is None:
        raise KeyError(conflict_id)
    det = c["details"]
    values = [x["value"] for x in det.get("variants", [])]
    if value not in values:
        raise ValueError(f"Вариант «{value}» не относится к этому противоречию")
    if det.get("area") == "responsibility":
        conn.execute("""UPDATE responsibilities SET status = 'confirmed', updated_at = datetime('now')
                        WHERE function = ? AND system = ? AND status IN ('auto', 'edited')""", (det["function"], value))
        conn.execute("""UPDATE responsibilities SET status = 'rejected', updated_at = datetime('now'),
                        comment = coalesce(comment || ' ', '') || ?
                        WHERE function = ? AND system != ? AND status IN ('auto', 'confirmed', 'edited')""",
                     (f"[отклонено при разборе противоречия #{conflict_id}]", det["function"], value))
    conn.execute("UPDATE conflicts SET status = 'resolved', resolution = ?, updated_at = datetime('now') WHERE id = ?",
                 (value, conflict_id))
    conn.commit()
    return get_conflict(conn, conflict_id)


def set_status(conn: sqlite3.Connection, conflict_id: int, status: str) -> dict:
    if status not in ("open", "dismissed"):
        raise ValueError(status)
    conn.execute("""UPDATE conflicts SET status = ?, resolution = CASE WHEN ? = 'open' THEN NULL ELSE resolution END,
                    updated_at = datetime('now') WHERE id = ?""", (status, status, conflict_id))
    conn.commit()
    return get_conflict(conn, conflict_id)


def question_text(c: dict) -> tuple[str, str, str]:
    """(вопрос, контекст, источники) для открытого вопроса заказчику."""
    det = c["details"]
    variants = det.get("variants", [])
    srcs = [s for var in variants for s in var["sources"]] + det.get("sources", [])
    sources = "; ".join(dict.fromkeys(s["label"] for s in srcs))
    context = "\n".join(f"{var['value']}: " + "; ".join(f"{s['label']} — «{(s.get('quote') or '')[:160]}»" for s in var["sources"])
                        for var in variants)
    vals = [x["value"] for x in variants]
    area = det.get("area")
    if c["kind"] == "contradiction" and area == "responsibility":
        text = f"Какая система отвечает за «{det['function_title']}»: {' или '.join(vals)}? В документах указаны разные варианты."
    elif c["kind"] == "contradiction" and area == "protocol":
        a, b = det["pair"]
        text = f"Какой протокол используется для обмена {a} ↔ {b}: {' или '.join(vals)}?"
    elif c["kind"] == "revision":
        cur = [x["value"] for x in variants if x["current"]]
        old = [x["value"] for x in variants if not x["current"]]
        text = f"Подтвердите, что «{det['function_title']}» выполняет {', '.join(cur)} (в ранней редакции — {', '.join(old)})."
    elif area == "responsibility":
        text = f"Какая система отвечает за «{det['function_title']}»? В документах это не указано."
    elif c["key"].startswith("gap:proto:"):
        text = f"По какому протоколу выполняется обмен: {det['title']}? В документах протокол не указан."
    else:
        text = f"Как организован обмен: {det.get('title', c['title'])}? В документах он не описан."
    return text, (context or c["title"])[:1000], sources


def to_question(conn: sqlite3.Connection, conflict_id: int) -> dict:
    c = get_conflict(conn, conflict_id)
    if c is None:
        raise KeyError(conflict_id)
    if c["question_id"] and one(conn, "SELECT id FROM open_questions WHERE id = ?", (c["question_id"],)):
        return c
    text, context, sources = question_text(c)
    cur = conn.execute("INSERT INTO open_questions(text, context, sources) VALUES (?, ?, ?)", (text, context, sources or None))
    conn.execute("UPDATE conflicts SET status = 'question', question_id = ?, updated_at = datetime('now') WHERE id = ?",
                 (cur.lastrowid, conflict_id))
    conn.commit()
    return get_conflict(conn, conflict_id)
