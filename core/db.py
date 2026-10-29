"""SQLite-хранилище: документы, блоки текста, фрагменты для поиска, требования, вопросы."""
from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS documents (
    id              INTEGER PRIMARY KEY,
    source_path     TEXT NOT NULL,          -- путь к файлу, из которого читали (может быть конвертированной копией)
    rel_path        TEXT NOT NULL,          -- путь внутри архива, как его видит пользователь
    sha256          TEXT NOT NULL UNIQUE,
    ext             TEXT NOT NULL,
    size_bytes      INTEGER,
    status          TEXT NOT NULL DEFAULT 'new',   -- new | parsed | needs_ocr | unsupported | error
    error           TEXT,
    pages           INTEGER,
    -- карточка документа
    title           TEXT,
    doc_type        TEXT,
    doc_date        TEXT,                   -- ISO yyyy-mm-dd, если удалось определить
    revision        TEXT,
    approval        TEXT,                   -- утверждён | проект | не указано
    side            TEXT,                   -- заказчик | подрядчик | не указано
    subsystems      TEXT,                   -- через запятую: EWM, MES, ERP, RMS...
    relevance       INTEGER DEFAULT 0,      -- 0..3 релевантность AMR/RMS
    relevance_score REAL DEFAULT 0,
    family_key      TEXT,                   -- ключ семейства редакций
    is_current      INTEGER DEFAULT 1,      -- 1 — актуальная редакция в своём семействе
    superseded_by   INTEGER REFERENCES documents(id),
    card_source     TEXT DEFAULT 'auto',    -- auto | manual
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS blocks (
    id       INTEGER PRIMARY KEY,
    doc_id   INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    seq      INTEGER NOT NULL,
    page     INTEGER,                       -- номер страницы (pdf) или листа (xlsx); для docx — оценка
    section  TEXT,                          -- ближайший заголовок
    kind     TEXT NOT NULL,                 -- heading | text | table
    text     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_blocks_doc ON blocks(doc_id, seq);

CREATE TABLE IF NOT EXISTS chunks (
    id        INTEGER PRIMARY KEY,
    doc_id    INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    seq       INTEGER NOT NULL,
    page_from INTEGER,
    page_to   INTEGER,
    section   TEXT,
    text      TEXT NOT NULL,
    embedding BLOB                          -- float32-вектор, если считали эмбеддинги
);
CREATE INDEX IF NOT EXISTS ix_chunks_doc ON chunks(doc_id);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    text, section, content='chunks', content_rowid='id', tokenize='unicode61 remove_diacritics 2'
);
CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
    INSERT INTO chunks_fts(rowid, text, section) VALUES (new.id, new.text, coalesce(new.section, ''));
END;
CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, text, section) VALUES ('delete', old.id, old.text, coalesce(old.section, ''));
END;

CREATE TABLE IF NOT EXISTS requirements (
    id         INTEGER PRIMARY KEY,
    code       TEXT UNIQUE,                 -- TR-001
    doc_id     INTEGER REFERENCES documents(id) ON DELETE CASCADE,
    chunk_id   INTEGER REFERENCES chunks(id) ON DELETE SET NULL,
    page       INTEGER,
    text       TEXT NOT NULL,
    target     TEXT,                        -- AMR | RMS | AMR+RMS | Другое
    category   TEXT,
    obligation TEXT,
    quote      TEXT,
    quote_score REAL,
    status     TEXT NOT NULL DEFAULT 'auto', -- auto | confirmed | edited | rejected
    comment    TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS open_questions (
    id         INTEGER PRIMARY KEY,
    text       TEXT NOT NULL,
    context    TEXT,                        -- откуда возник вопрос: ответ, требование, противоречие
    sources    TEXT,                        -- ссылки на документы через ';'
    status     TEXT NOT NULL DEFAULT 'open', -- open | sent | answered | closed
    answer     TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS extraction_log (
    chunk_id INTEGER NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
    kind     TEXT NOT NULL,                 -- requirements | processes | interfaces ...
    found    INTEGER,
    error    TEXT,
    done_at  TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (chunk_id, kind)
);

CREATE TABLE IF NOT EXISTS runs (
    id         INTEGER PRIMARY KEY,
    stage      TEXT NOT NULL,
    started_at TEXT DEFAULT (datetime('now')),
    finished_at TEXT,
    info       TEXT
);
"""


def connect(db_file: Path | str) -> sqlite3.Connection:
    db_file = Path(db_file)
    if str(db_file) != ":memory:":
        db_file.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_file), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL") if str(db_file) != ":memory:" else None
    conn.executescript(SCHEMA)
    return conn


def rows(conn: sqlite3.Connection, sql: str, params: tuple | dict = ()) -> list[dict]:
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def one(conn: sqlite3.Connection, sql: str, params: tuple | dict = ()) -> dict | None:
    r = conn.execute(sql, params).fetchone()
    return dict(r) if r else None


def update(conn: sqlite3.Connection, table: str, row_id: int, **fields) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(f"UPDATE {table} SET {cols} WHERE id = ?", (*fields.values(), row_id))
    conn.commit()
