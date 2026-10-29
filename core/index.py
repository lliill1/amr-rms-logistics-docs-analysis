"""Фрагменты для поиска и гибридный поиск: полнотекстовый (SQLite FTS5) + векторный (эмбеддинги), слияние RRF."""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

import numpy as np

from . import glossary
from .db import rows

STOPWORDS = set("""и в во не что он на я с со как а то все она так его но да ты к у же вы за бы по только ее мне было вот от
меня еще нет о из ему теперь когда даже ну вдруг ли если уже или ни быть был него до вас нибудь опять уж вам ведь там потом
себя ничего ей может они тут где есть надо ней для мы тебя их чем была сам чтоб без будто чего раз тоже себе под будет ж тогда
кто этот того потому этого какой совсем ним здесь этом один почти мой тем чтобы нее сейчас были куда зачем всех никогда можно
при наконец два об другой хоть после над больше тот через эти нас про всего них какая много разве три эту моя впрочем хорошо
свою этой перед иногда лучше чуть том нельзя такой им более всегда конечно всю между какие какое каких который которая которые
должен должна должно должны the a an of to in for and or is are""".split())


@dataclass
class Hit:
    chunk_id: int
    doc_id: int
    text: str
    section: str | None
    page_from: int | None
    page_to: int | None
    rel_path: str
    title: str | None
    is_current: int
    approval: str | None
    side: str | None
    score: float


# ---------- нарезка ----------

def make_chunks(blocks: list[dict], max_chars: int = 2500, overlap: int = 200) -> list[dict]:
    """Склеивает блоки одного раздела в фрагменты до max_chars. Заголовок начинает новый фрагмент;
    слишком длинные блоки (большие таблицы) режутся по строкам."""
    chunks: list[dict] = []
    cur: list[str] = []
    cur_section = None
    page_from = page_to = None

    def emit():
        text = "\n".join(cur).strip()
        if text:
            chunks.append({"text": text, "section": cur_section, "page_from": page_from, "page_to": page_to})

    for b in blocks:
        page = b.get("page")
        # новый раздел — новый фрагмент, без перекрытия
        if b["kind"] == "heading" or b.get("section") != cur_section:
            emit()
            cur, cur_section, page_from, page_to = [], b.get("section"), page, page
        pieces = _split_long(b["text"], max_chars) if len(b["text"]) > max_chars else [b["text"]]
        for piece in pieces:
            if cur and sum(len(x) + 1 for x in cur) + len(piece) > max_chars:
                emit()
                prev = "\n".join(cur)
                tail = prev[-overlap:] if overlap else ""
                cur, page_from = ([tail] if tail else []), page_to
            cur.append(piece)
            page_to = page
            if page_from is None:
                page_from = page
    emit()
    return chunks


def _split_long(text: str, max_chars: int) -> list[str]:
    lines = text.split("\n")
    header = lines[0] if " | " in lines[0] else None  # таблица: повторяем шапку
    out, buf, size = [], [], 0
    for line in lines:
        if size + len(line) > max_chars and buf:
            out.append("\n".join(buf))
            buf, size = ([header] if header else []), len(header or "")
        if len(line) > max_chars:  # очень длинная строка без переносов
            for i in range(0, len(line), max_chars):
                out.append(line[i:i + max_chars])
            continue
        buf.append(line)
        size += len(line) + 1
    if buf:
        out.append("\n".join(buf))
    return out


def index_document(conn: sqlite3.Connection, doc_id: int, max_chars: int, overlap: int) -> int:
    conn.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
    blocks = rows(conn, "SELECT kind, text, page, section FROM blocks WHERE doc_id = ? ORDER BY seq", (doc_id,))
    chunks = make_chunks(blocks, max_chars, overlap)
    conn.executemany(
        "INSERT INTO chunks(doc_id, seq, page_from, page_to, section, text) VALUES (?, ?, ?, ?, ?, ?)",
        [(doc_id, i, c["page_from"], c["page_to"], c["section"], c["text"]) for i, c in enumerate(chunks)],
    )
    conn.commit()
    return len(chunks)


def embed_missing(conn: sqlite3.Connection, llm, batch: int = 32, progress=None) -> int:
    """Считает эмбеддинги для фрагментов, у которых их ещё нет."""
    todo = rows(conn, """SELECT c.id, c.text, c.section, d.title FROM chunks c JOIN documents d ON d.id = c.doc_id
                         WHERE c.embedding IS NULL ORDER BY c.id""")
    done = 0
    for i in range(0, len(todo), batch):
        part = todo[i:i + batch]
        texts = [_embed_text(r) for r in part]
        vecs = llm.embed(texts)
        conn.executemany("UPDATE chunks SET embedding = ? WHERE id = ?",
                         [(v.astype(np.float32).tobytes(), r["id"]) for v, r in zip(vecs, part)])
        conn.commit()
        done += len(part)
        if progress:
            progress(done, len(todo))
    return done


def _embed_text(r: dict) -> str:
    head = " / ".join(x for x in (r.get("title"), r.get("section")) if x)
    return (head + "\n" + r["text"])[:6000]


# ---------- поиск ----------

def _fts_terms(query: str) -> list[str]:
    words = re.findall(r"[\wёЁ\-/]+", query.lower())
    terms = []
    for w in words:
        w = w.strip("-/")
        if len(w) < 2 or w in STOPWORDS:
            continue
        if re.search(r"[а-яё]", w) and len(w) > 5:
            w = w[: max(4, len(w) - 2)]  # грубый стемминг: отрезаем окончание
        w = re.sub(r"[^\wё]", " ", w).strip()
        for part in w.split():
            terms.append(f'"{part}"*')
    return list(dict.fromkeys(terms))


def fts_search(conn: sqlite3.Connection, query: str, limit: int = 30, only_current: bool = True) -> list[tuple[int, float]]:
    terms = _fts_terms(query + " " + " ".join(glossary.expand_query(query)))
    if not terms:
        return []
    match = " OR ".join(terms)
    cond = "AND d.is_current = 1" if only_current else ""
    sql = f"""SELECT c.id, bm25(chunks_fts) AS s FROM chunks_fts
              JOIN chunks c ON c.id = chunks_fts.rowid JOIN documents d ON d.id = c.doc_id
              WHERE chunks_fts MATCH ? {cond} ORDER BY s LIMIT ?"""
    return [(r[0], -r[1]) for r in conn.execute(sql, (match, limit)).fetchall()]


def vector_search(conn: sqlite3.Connection, query_vec: np.ndarray, limit: int = 30,
                  only_current: bool = True) -> list[tuple[int, float]]:
    cond = "AND d.is_current = 1" if only_current else ""
    data = conn.execute(f"""SELECT c.id, c.embedding FROM chunks c JOIN documents d ON d.id = c.doc_id
                            WHERE c.embedding IS NOT NULL {cond}""").fetchall()
    if not data:
        return []
    ids = np.array([r[0] for r in data])
    mat = np.vstack([np.frombuffer(r[1], dtype=np.float32) for r in data])
    q = query_vec.reshape(-1).astype(np.float32)
    if mat.shape[1] != q.shape[0]:
        return []
    sims = mat @ q
    top = np.argsort(-sims)[:limit]
    return [(int(ids[i]), float(sims[i])) for i in top]


def rrf(*ranked: list[tuple[int, float]], k: int = 60) -> list[tuple[int, float]]:
    scores: dict[int, float] = {}
    for lst in ranked:
        for rank, (cid, _s) in enumerate(lst):
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores.items(), key=lambda x: -x[1])


def search(conn: sqlite3.Connection, query: str, llm=None, top_k: int = 6, only_current: bool = True,
           use_vectors: bool = True) -> list[Hit]:
    lists = [fts_search(conn, query, only_current=only_current)]
    if use_vectors and llm is not None:
        try:
            qv = llm.embed([query])
            if qv.size:
                lists.append(vector_search(conn, qv[0], only_current=only_current))
        except Exception:  # noqa: BLE001 — без эмбеддингов работаем только на полнотексте
            pass
    merged = rrf(*lists)[:top_k]
    if not merged:
        return []
    ids = [cid for cid, _ in merged]
    meta = {r["id"]: r for r in rows(conn, f"""
        SELECT c.id, c.doc_id, c.text, c.section, c.page_from, c.page_to,
               d.rel_path, d.title, d.is_current, d.approval, d.side
        FROM chunks c JOIN documents d ON d.id = c.doc_id WHERE c.id IN ({",".join("?" * len(ids))})""", ids)}
    return [Hit(chunk_id=cid, score=s, **{k: v for k, v in meta[cid].items() if k != "id"}) for cid, s in merged if cid in meta]
