"""Переносит данные текущей SQLite-базы в пустую PostgreSQL-базу."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import db  # noqa: E402
from core.config import load_settings  # noqa: E402

TABLES = (
    "documents", "blocks", "chunks", "requirements", "open_questions",
    "processes", "interfaces", "responsibilities", "conflicts",
    "extraction_log", "runs",
)
SERIAL_TABLES = tuple(table for table in TABLES if table != "extraction_log")
BATCH_SIZE = 500


def migrate(source_path: Path, target) -> int:
    if not source_path.is_file():
        raise FileNotFoundError(f"Исходная SQLite-база не найдена: {source_path}")
    source = db.connect(source_path)
    try:
        counts = {
            table: target.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"]
            for table in TABLES
        }
        if any(counts.values()):
            raise RuntimeError("Целевая PostgreSQL-база не пуста; перенос остановлен без изменений.")

        copied = 0
        try:
            for table in TABLES:
                target_columns = [column.name for column in target.execute(f"SELECT * FROM {table} LIMIT 0").description]
                source_columns = {row["name"] for row in source.execute(f"PRAGMA table_info({table})")}
                columns = [name for name in target_columns if name in source_columns and name != "tsv"]
                if not columns:
                    continue
                quoted = ", ".join(f'"{name}"' for name in columns)
                placeholders = ", ".join("?" for _ in columns)
                select = source.execute(f"SELECT {quoted} FROM {table}")
                insert = f"INSERT INTO {table} ({quoted}) VALUES ({placeholders})"
                while True:
                    batch = select.fetchmany(BATCH_SIZE)
                    if not batch:
                        break
                    target.executemany(insert, [tuple(row[name] for name in columns) for row in batch])
                    copied += len(batch)

            for table in SERIAL_TABLES:
                target.execute(
                    f"""SELECT setval(pg_get_serial_sequence('{table}', 'id'),
                              COALESCE(MAX(id), 1), MAX(id) IS NOT NULL) FROM {table}"""
                )
            target.commit()
        except Exception:
            target.rollback()
            raise
        return copied
    finally:
        source.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Перенос SQLite-базы DocAnalyzer в PostgreSQL")
    parser.add_argument("--source", type=Path, default=load_settings().db_file,
                        help="путь к исходной SQLite-базе")
    args = parser.parse_args()
    settings = load_settings()
    if not settings.database_url:
        parser.error("задайте DOCANALYZER_DATABASE_URL или database.url в config/settings.local.yaml")
    target = db.connect(settings.database_target)
    try:
        count = migrate(args.source, target)
        print(f"Перенос завершён: {count} записей.")
    finally:
        target.close()


if __name__ == "__main__":
    main()
