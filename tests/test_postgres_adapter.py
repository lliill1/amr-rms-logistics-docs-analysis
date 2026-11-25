from core.db import _postgres_sql


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _FakePostgres:
    dialect = "postgresql"

    def __init__(self, result):
        self.result = result
        self.sql = None
        self.params = None

    def execute(self, sql, params=()):
        self.sql = sql
        self.params = params
        return self.result


def test_postgres_sql_converts_placeholders_without_touching_literals():
    sql = "SELECT ?, ':ignored?', name FROM docs WHERE id = :doc_id AND created_at > datetime('now')"
    assert _postgres_sql(sql) == (
        "SELECT %s, ':ignored?', name FROM docs WHERE id = %(doc_id)s "
        "AND created_at > CURRENT_TIMESTAMP"
    )


def test_postgres_sql_preserves_postgres_casts():
    assert _postgres_sql("SELECT value::text FROM items WHERE id = ?") == (
        "SELECT value::text FROM items WHERE id = %s"
    )


def test_postgres_sql_escapes_literal_percent_for_psycopg():
    assert _postgres_sql("SELECT id FROM items WHERE code LIKE 'TR-%'") == (
        "SELECT id FROM items WHERE code LIKE 'TR-%%'"
    )


def test_postgres_full_text_search_uses_tsvector(monkeypatch):
    from core import index

    conn = _FakePostgres(_Result([{"id": 8, "s": 0.4}]))
    monkeypatch.setattr(index.glossary, "expand_query", lambda _query: [])

    assert index.fts_search(conn, "поставка") == [(8, 0.4)]
    assert "to_tsquery('russian'" in conn.sql
    assert conn.params[0] == conn.params[1]
    assert conn.params[2] == 30


def test_postgres_vector_search_reads_bytea_embeddings():
    import numpy as np
    from core.index import vector_search

    embedding = np.array([1.0, 0.0], dtype=np.float32).tobytes()
    conn = _FakePostgres(_Result([{"id": 11, "embedding": embedding}]))

    assert vector_search(conn, np.array([1.0, 0.0], dtype=np.float32)) == [(11, 1.0)]


def test_database_url_environment_overrides_config(monkeypatch):
    from core.config import load_settings

    monkeypatch.setenv("DOCANALYZER_DATABASE_URL", "postgresql://env/db")
    settings = load_settings(overrides={"database": {"url": "postgresql://config/db"}})

    assert settings.database_target == "postgresql://env/db"


def test_missing_postgres_app_schema_reports_incompatible_tables():
    from core.db import _missing_postgres_app_schema

    class SchemaResult:
        def fetchall(self):
            return [
                {"table_name": "documents", "column_name": "id"},
                {"table_name": "chunks", "column_name": "document_id"},
            ]

    class SchemaConnection:
        def execute(self, _sql):
            return SchemaResult()

    missing = _missing_postgres_app_schema(SchemaConnection())

    assert any("documents" in item for item in missing)
    assert any("chunks.doc_id" in item for item in missing)
    assert any("таблица отсутствует" in item for item in missing)
