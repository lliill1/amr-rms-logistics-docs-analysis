"""Конвейер обработки архива: файлы -> текст -> карточки -> редакции -> поисковый индекс -> эмбеддинги.

Каждый этап можно перезапускать: уже загруженные файлы (по SHA-256) повторно не разбираются.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import catalog, index
from .config import Settings
from .db import one, rows
from .ingest import CONVERT_MAP, collect_files, convert_with_libreoffice, find_soffice, sha256_of
from .parsers import SUPPORTED_EXTS, UnsupportedFormat, parse_file

Progress = Callable[[str, int, int], None]  # (этап, сделано, всего)


@dataclass
class RunStats:
    found: int = 0
    added: int = 0
    duplicates: int = 0
    parsed: int = 0
    needs_ocr: int = 0
    unsupported: int = 0
    errors: int = 0
    superseded: int = 0
    chunks: int = 0
    embedded: int = 0
    skipped: list[tuple[str, str]] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)


def _noop(*_a):
    pass


def ingest_source(conn: sqlite3.Connection, settings: Settings, source: Path, progress: Progress = _noop) -> RunStats:
    """Этапы 1–2: собрать файлы, сконвертировать старые форматы, извлечь текст."""
    stats = RunStats()
    work = settings.data_dir
    report = collect_files(Path(source), work)
    stats.found = len(report.files)
    stats.skipped += report.skipped
    soffice = find_soffice(settings.get("libreoffice.soffice_path") or None)
    if soffice is None and any(f.path.suffix.lower() in CONVERT_MAP for f in report.files):
        stats.messages.append("LibreOffice не найден: файлы .doc/.xls/.ppt/.vsd не будут прочитаны. "
                              "Установите LibreOffice или укажите путь в settings.yaml.")
    min_chars = int(settings.get("processing.min_text_chars_per_page", 40))

    for i, f in enumerate(report.files, start=1):
        progress("Извлечение текста", i, len(report.files))
        try:
            sha = sha256_of(f.path)
        except OSError as e:
            stats.skipped.append((f.rel_path, f"не читается: {e}"))
            continue
        if one(conn, "SELECT id FROM documents WHERE sha256 = ?", (sha,)):
            stats.duplicates += 1
            continue

        ext = f.path.suffix.lower()
        cur = conn.execute(
            "INSERT INTO documents(source_path, rel_path, sha256, ext, size_bytes, status) VALUES (?, ?, ?, ?, ?, 'new')",
            (str(f.path), f.rel_path, sha, ext, f.path.stat().st_size))
        doc_id = cur.lastrowid
        stats.added += 1

        read_path = f.path
        try:
            if ext in CONVERT_MAP:
                if soffice is None:
                    raise UnsupportedFormat(f"{ext}: нужен LibreOffice")
                read_path = convert_with_libreoffice(f.path, CONVERT_MAP[ext], work / "converted" / sha[:12], soffice)
                conn.execute("UPDATE documents SET source_path = ? WHERE id = ?", (str(read_path), doc_id))
            if read_path.suffix.lower() not in SUPPORTED_EXTS:
                raise UnsupportedFormat(ext or "без расширения")
            parsed = parse_file(read_path, min_text_chars_per_page=min_chars)
        except UnsupportedFormat as e:
            conn.execute("UPDATE documents SET status = 'unsupported', error = ? WHERE id = ?", (f"формат не поддерживается: {e}", doc_id))
            stats.unsupported += 1
            conn.commit()
            continue
        except Exception as e:  # noqa: BLE001 — один битый файл не должен ронять весь архив
            conn.execute("UPDATE documents SET status = 'error', error = ? WHERE id = ?", (str(e)[:500], doc_id))
            stats.errors += 1
            conn.commit()
            continue

        conn.executemany(
            "INSERT INTO blocks(doc_id, seq, page, section, kind, text) VALUES (?, ?, ?, ?, ?, ?)",
            [(doc_id, n, b.page, b.section, b.kind, b.text) for n, b in enumerate(parsed.blocks)])
        status = "needs_ocr" if parsed.needs_ocr else "parsed"
        conn.execute("UPDATE documents SET status = ?, pages = ?, title = ? WHERE id = ?",
                     (status, parsed.pages, parsed.title, doc_id))
        conn.commit()
        if status == "needs_ocr":
            stats.needs_ocr += 1
        else:
            stats.parsed += 1
    return stats


def build_catalog(conn: sqlite3.Connection, settings: Settings, progress: Progress = _noop) -> int:
    """Этап 3: карточки документов и выбор актуальных редакций. Ручные правки не перезаписываются."""
    customer = [m.lower() for m in settings.get("catalog.customer_markers", []) or []]
    contractor = [m.lower() for m in settings.get("catalog.contractor_markers", []) or []]
    docs = rows(conn, "SELECT id, rel_path, title FROM documents WHERE status IN ('parsed', 'needs_ocr')")
    for i, d in enumerate(docs, start=1):
        progress("Карточки документов", i, len(docs))
        blocks = rows(conn, "SELECT text FROM blocks WHERE doc_id = ? ORDER BY seq", (d["id"],))
        full = "\n".join(b["text"] for b in blocks)
        head = full[:4000]
        card = catalog.build_card(d["rel_path"], d["title"], full, head, customer, contractor)
        catalog.save_card(conn, d["id"], card)
    conn.commit()
    return catalog.resolve_families(conn)


def build_index(conn: sqlite3.Connection, settings: Settings, progress: Progress = _noop, only_new: bool = True) -> int:
    """Этап 4а: нарезка на фрагменты и полнотекстовый индекс."""
    max_chars = int(settings.get("processing.chunk_max_chars", 2500))
    overlap = int(settings.get("processing.chunk_overlap_chars", 200))
    cond = "AND NOT EXISTS (SELECT 1 FROM chunks c WHERE c.doc_id = d.id)" if only_new else ""
    docs = rows(conn, f"SELECT d.id FROM documents d WHERE d.status = 'parsed' {cond}")
    total = 0
    for i, d in enumerate(docs, start=1):
        progress("Поисковый индекс", i, len(docs))
        total += index.index_document(conn, d["id"], max_chars, overlap)
    return total


def build_embeddings(conn: sqlite3.Connection, llm, progress: Progress = _noop) -> int:
    """Этап 4б: векторы для смыслового поиска (нужна Ollama с моделью эмбеддингов)."""
    return index.embed_missing(conn, llm, progress=lambda done, total: progress("Эмбеддинги", done, total))


def run_all(conn: sqlite3.Connection, settings: Settings, source: Path, llm=None, progress: Progress = _noop) -> RunStats:
    stats = ingest_source(conn, settings, source, progress)
    stats.superseded = build_catalog(conn, settings, progress)
    stats.chunks = build_index(conn, settings, progress)
    if llm is not None and settings.get("search.use_vectors", True):
        try:
            stats.embedded = build_embeddings(conn, llm, progress)
        except Exception as e:  # noqa: BLE001
            stats.messages.append(f"Эмбеддинги не посчитаны ({e}). Поиск будет работать только по словам.")
    conn.execute("INSERT INTO runs(stage, finished_at, info) VALUES ('run_all', datetime('now'), ?)",
                 (json.dumps({k: v for k, v in stats.__dict__.items() if k != "skipped"}, ensure_ascii=False),))
    conn.commit()
    return stats
