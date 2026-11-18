"""Карточка документа (тип, дата, редакция, статус, сторона, подсистемы, релевантность)
и группировка редакций одного документа в семейства с выбором актуальной."""
from __future__ import annotations

import re
import sqlite3
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import PurePosixPath

from . import glossary
from .db import rows

# порядок важен: первое совпадение побеждает
DOC_TYPES: list[tuple[str, list[str]]] = [
    ("Протокол", [r"протокол", r"\bminutes\b", r"\bmom\b"]),
    ("Техническое задание", [r"техническ\w* задани", r"\bтз\b", r"\btor\b"]),
    ("Проектное решение", [r"проектн\w* решени", r"\bпр[_ ]", r"blueprint", r"\bbbp\b", r"концептуальн\w* проект",
                           r"функциональн\w* спецификац", r"\bfs\b", r"\bfrs\b"]),
    ("Спецификация интерфейса", [r"интеграц", r"интерфейс", r"маппинг", r"mapping", r"\bidoc\b", r"\bapi\b"]),
    ("Описание бизнес-процесса", [r"бизнес[- ]процесс", r"описани\w* процесс", r"\bбп\b", r"регламент"]),
    ("Схема", [r"схем", r"\.vsdx?$", r"планировк", r"layout"]),
    ("Презентация", [r"презентац", r"\.pptx?$"]),
    ("Письмо", [r"\.eml$", r"\.msg$", r"письм"]),
]

MONTHS = {"январ": 1, "феврал": 2, "март": 3, "апрел": 4, "ма": 5, "июн": 6, "июл": 7, "август": 8,
          "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12}

RE_DMY = re.compile(r"(?<!\d)(\d{1,2})[./](\d{1,2})[./](\d{4}|\d{2})(?!\d)")
RE_YMD = re.compile(r"(?<!\d)(20\d{2})[-_.](\d{2})[-_.](\d{2})(?!\d)")
RE_TEXT_DATE = re.compile(r"«?(\d{1,2})»?\s+(январ|феврал|март|апрел|ма|июн|июл|август|сентябр|октябр|ноябр|декабр)\w*\s+(\d{4})",
                          re.IGNORECASE)
RE_REVISION = re.compile(
    r"(?:редакци[яи]|ред\.?|верси[яи]|вер\.|rev(?:ision)?\.?|(?<![a-zа-я])v)\s*[._№ ]?\s*(\d{1,3}(?:[._]\d{1,3}){0,2})",
    re.IGNORECASE)
RE_APPROVED = re.compile(r"\bутверждаю\b|\bутвержден\w*|\bсогласовано\b|\bfinal\b|итогов\w* редакц", re.IGNORECASE)
RE_DRAFT = re.compile(r"\bпроект\b(?! ?(решени|роботизац|внедрени|ewm|sap))|\bчерновик|\bdraft\b|\bна согласовани",
                      re.IGNORECASE)

SUBSYSTEMS = {
    "EWM": r"\bewm\b|extended warehouse",
    "ERP": r"\berp\b|s/?4\s?hana",
    "MES": r"\bmes\b",
    "RMS": r"\brms\b|fleet manag|систем\w* управлени\w* робот",
    "AMR": r"\bamr\b|\bagv\b|мобильн\w* робот",
    "WMS": r"\bwms\b",
    "PLC": r"\bplc\b|\bпл[кc]\b|контроллер",
}


@dataclass
class Card:
    title: str | None
    doc_type: str
    doc_date: str | None
    revision: str | None
    approval: str
    side: str
    subsystems: str
    relevance: int
    relevance_score: float
    family_key: str


def _valid(y: int, m: int, d: int) -> date | None:
    if y < 100:
        y += 2000
    if not (1995 <= y <= 2040):
        return None
    try:
        return date(y, m, d)
    except ValueError:
        return None


def find_dates(text: str) -> list[date]:
    found: list[date] = []
    for d, m, y in RE_DMY.findall(text):
        dt = _valid(int(y), int(m), int(d))
        if dt:
            found.append(dt)
    for y, m, d in RE_YMD.findall(text):
        dt = _valid(int(y), int(m), int(d))
        if dt:
            found.append(dt)
    for d, mon, y in RE_TEXT_DATE.findall(text):
        m = next((v for k, v in MONTHS.items() if mon.lower().startswith(k)), None)
        dt = _valid(int(y), m, int(d)) if m else None
        if dt:
            found.append(dt)
    return found


def detect_date(name: str, head: str) -> str | None:
    in_name = find_dates(name)
    if in_name:
        return max(in_name).isoformat()
    # дата рядом с «от», «дата» — обычно дата документа
    m = re.search(r"(?:\bот\b|дата[^\n:]{0,20}:?)\s*(.{0,30})", head, re.IGNORECASE)
    if m:
        near = find_dates(m.group(1))
        if near:
            return near[0].isoformat()
    in_head = find_dates(head[:3000])
    return max(in_head).isoformat() if in_head else None


def detect_revision(name: str, head: str) -> str | None:
    for text in (name, head[:3000]):
        m = RE_REVISION.search(text)
        if m:
            return m.group(1).replace("_", ".")
    return None


def detect_type(rel_path: str, title: str, head: str) -> str:
    name = PurePosixPath(rel_path).name.lower()
    path = rel_path.lower()
    for source in (name, (title or "").lower(), head[:1500].lower(), path):
        for doc_type, patterns in DOC_TYPES:
            if any(re.search(p, source) for p in patterns):
                return doc_type
    return "Прочее"


def detect_approval(head: str) -> str:
    h = head[:3000]
    if RE_APPROVED.search(h):
        return "утверждён"
    if RE_DRAFT.search(h):
        return "проект"
    return "не указано"


def detect_side(rel_path: str, head: str, customer: list[str], contractor: list[str]) -> str:
    text = (rel_path + "\n" + head[:2000]).lower()
    c = sum(text.count(m) for m in customer)
    k = sum(text.count(m) for m in contractor)
    if c > k:
        return "заказчик"
    if k > c:
        return "подрядчик"
    return "не указано"


def detect_subsystems(text: str) -> str:
    t = text.lower()
    return ", ".join(k for k, p in SUBSYSTEMS.items() if re.search(p, t))


_FAMILY_NOISE = [
    RE_REVISION,
    re.compile(r"(?<!\d)\d{1,2}[./]\d{1,2}[./]\d{2,4}(?!\d)"),
    re.compile(r"(?<!\d)20\d{2}[-_.]\d{2}[-_.]\d{2}(?!\d)"),
    re.compile(r"\b(old|new|final|draft|copy|копия|старая|новая|итог\w*|финал\w*|черновик|исправл\w*)\b", re.IGNORECASE),
    re.compile(r"\(\d+\)"),
]


def family_key(rel_path: str) -> str:
    stem = PurePosixPath(rel_path).stem.lower()
    for rx in _FAMILY_NOISE:
        stem = rx.sub(" ", stem)
    stem = re.sub(r"[\s_\-.,]+", " ", stem).strip()
    return stem or PurePosixPath(rel_path).stem.lower()


def build_card(rel_path: str, title: str | None, full_text: str, head_text: str,
               customer_markers: list[str], contractor_markers: list[str]) -> Card:
    name = PurePosixPath(rel_path).name
    level, score = glossary.relevance(full_text + " " + rel_path)
    return Card(
        title=(title or PurePosixPath(rel_path).stem)[:300],
        doc_type=detect_type(rel_path, title or "", head_text),
        doc_date=detect_date(name, head_text),
        revision=detect_revision(name, head_text),
        approval=detect_approval(head_text),
        side=detect_side(rel_path, head_text, customer_markers, contractor_markers),
        subsystems=detect_subsystems(full_text[:200_000]),
        relevance=level,
        relevance_score=round(score, 1),
        family_key=family_key(rel_path),
    )


def save_card(conn: sqlite3.Connection, doc_id: int, card: Card) -> None:
    fields = asdict(card)
    cols = ", ".join(f"{k} = :{k}" for k in fields)
    conn.execute(f"UPDATE documents SET {cols} WHERE id = :id AND card_source = 'auto'", {**fields, "id": doc_id})
    # family_key нужен и для документов с ручной карточкой
    conn.execute("UPDATE documents SET family_key = ? WHERE id = ? AND family_key IS NULL", (card.family_key, doc_id))


def _rev_tuple(rev: str | None) -> tuple:
    if not rev:
        return (-1,)
    return tuple(int(x) for x in re.findall(r"\d+", rev)) or (-1,)


def resolve_families(conn: sqlite3.Connection) -> int:
    """В каждом семействе редакций актуальной считается самая новая по номеру редакции, затем по дате,
    при равенстве — утверждённая. Спорные случаи аналитик правит вручную в карточке.
    Ручные пометки (card_source = 'manual') не трогаем. Возвращает число документов, признанных устаревшими."""
    docs = rows(conn, "SELECT id, family_key, revision, doc_date, approval, card_source, is_current, status "
                      "FROM documents WHERE status IN ('parsed', 'needs_ocr')")
    families: dict[str, list[dict]] = {}
    for d in docs:
        families.setdefault(d["family_key"] or f"#{d['id']}", []).append(d)

    superseded = 0
    for members in families.values():
        if len(members) == 1:
            d = members[0]
            if d["card_source"] == "auto":
                conn.execute("UPDATE documents SET is_current = 1, superseded_by = NULL WHERE id = ?", (d["id"],))
            continue
        best = max(members, key=lambda d: (_rev_tuple(d["revision"]), d["doc_date"] or "", d["approval"] == "утверждён", d["id"]))
        for d in members:
            if d["card_source"] != "auto":
                continue
            if d["id"] == best["id"]:
                conn.execute("UPDATE documents SET is_current = 1, superseded_by = NULL WHERE id = ?", (d["id"],))
            else:
                conn.execute("UPDATE documents SET is_current = 0, superseded_by = ? WHERE id = ?", (best["id"], d["id"]))
                superseded += 1
    conn.commit()
    return superseded
