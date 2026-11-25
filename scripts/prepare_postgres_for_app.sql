-- Запускать только с учетной записью-владельцем базы DocAnalyzer.
-- Безопасно повторять: все изменения идемпотентны.
\set ON_ERROR_STOP on

BEGIN;

CREATE TABLE IF NOT EXISTS blocks (
  id BIGSERIAL PRIMARY KEY,
  doc_id BIGINT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  seq INTEGER NOT NULL,
  page INTEGER,
  section TEXT,
  kind TEXT NOT NULL,
  text TEXT NOT NULL
);

ALTER TABLE documents ADD COLUMN IF NOT EXISTS source_path TEXT;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS rel_path TEXT;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS sha256 TEXT;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS ext TEXT;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS size_bytes BIGINT;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'new';
ALTER TABLE documents ADD COLUMN IF NOT EXISTS error TEXT;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS pages INTEGER;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS ocr_pages INTEGER NOT NULL DEFAULT 0;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS doc_type TEXT;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS revision TEXT;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS approval TEXT;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS side TEXT;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS subsystems TEXT;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS relevance INTEGER NOT NULL DEFAULT 0;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS relevance_score DOUBLE PRECISION NOT NULL DEFAULT 0;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS family_key TEXT;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS is_current INTEGER NOT NULL DEFAULT 1;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS superseded_by BIGINT REFERENCES documents(id);
ALTER TABLE documents ADD COLUMN IF NOT EXISTS card_source TEXT NOT NULL DEFAULT 'auto';

UPDATE documents
SET sha256 = md5(COALESCE(sha256, '') || ':' || id::text)
WHERE sha256 IS NULL;
UPDATE documents
SET source_path = COALESCE(source_path, source_name, title, sha256)
WHERE source_path IS NULL;
UPDATE documents
SET rel_path = COALESCE(rel_path, source_name, title, sha256)
WHERE rel_path IS NULL;
UPDATE documents
SET ext = COALESCE(NULLIF(ext, ''), 'unknown')
WHERE ext IS NULL OR ext = '';

ALTER TABLE documents ALTER COLUMN source_path SET NOT NULL;
ALTER TABLE documents ALTER COLUMN rel_path SET NOT NULL;
ALTER TABLE documents ALTER COLUMN sha256 SET NOT NULL;
ALTER TABLE documents ALTER COLUMN ext SET NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS documents_sha256_key ON documents (sha256);

ALTER TABLE chunks ADD COLUMN IF NOT EXISTS doc_id BIGINT;
ALTER TABLE chunks ADD COLUMN IF NOT EXISTS seq INTEGER;
ALTER TABLE chunks ADD COLUMN IF NOT EXISTS page_from INTEGER;
ALTER TABLE chunks ADD COLUMN IF NOT EXISTS page_to INTEGER;
ALTER TABLE chunks ADD COLUMN IF NOT EXISTS section TEXT;
ALTER TABLE chunks ADD COLUMN IF NOT EXISTS embedding BYTEA;

DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM information_schema.columns
    WHERE table_schema = current_schema()
      AND table_name = 'chunks'
      AND column_name = 'embedding'
      AND udt_name = 'vector'
  ) THEN
    ALTER TABLE chunks RENAME COLUMN embedding TO embedding_vector;
    ALTER TABLE chunks ADD COLUMN embedding BYTEA;
  END IF;
END $$;

ALTER TABLE chunks ADD COLUMN IF NOT EXISTS tsv tsvector;

DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM information_schema.columns
    WHERE table_schema = current_schema()
      AND table_name = 'chunks'
      AND column_name = 'document_id'
  ) THEN
    UPDATE chunks SET doc_id = document_id WHERE doc_id IS NULL;
  END IF;
END $$;

UPDATE chunks SET seq = COALESCE(seq, id);
ALTER TABLE chunks ALTER COLUMN seq SET NOT NULL;
ALTER TABLE chunks ALTER COLUMN doc_id SET NOT NULL;
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conname = 'chunks_doc_id_fkey'
      AND conrelid = 'chunks'::regclass
  ) THEN
    ALTER TABLE chunks
      ADD CONSTRAINT chunks_doc_id_fkey
      FOREIGN KEY (doc_id) REFERENCES documents(id) ON DELETE CASCADE
      NOT VALID;
    ALTER TABLE chunks VALIDATE CONSTRAINT chunks_doc_id_fkey;
  END IF;
END $$;

CREATE INDEX IF NOT EXISTS ix_chunks_doc ON chunks(doc_id, seq);
UPDATE chunks
SET tsv = to_tsvector('russian', coalesce(text, ''))
WHERE tsv IS NULL;
CREATE INDEX IF NOT EXISTS ix_chunks_tsv ON chunks USING gin(tsv);

CREATE TABLE IF NOT EXISTS requirements (
  id BIGSERIAL PRIMARY KEY,
  code TEXT UNIQUE,
  doc_id BIGINT REFERENCES documents(id) ON DELETE CASCADE,
  chunk_id BIGINT REFERENCES chunks(id) ON DELETE SET NULL,
  page INTEGER,
  text TEXT NOT NULL,
  target TEXT,
  category TEXT,
  obligation TEXT,
  quote TEXT,
  quote_score DOUBLE PRECISION,
  status TEXT NOT NULL DEFAULT 'auto',
  comment TEXT,
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ
);

ALTER TABLE requirements ADD COLUMN IF NOT EXISTS doc_id BIGINT REFERENCES documents(id) ON DELETE CASCADE;
ALTER TABLE requirements ADD COLUMN IF NOT EXISTS chunk_id BIGINT REFERENCES chunks(id) ON DELETE SET NULL;
ALTER TABLE requirements ADD COLUMN IF NOT EXISTS page INTEGER;
ALTER TABLE requirements ADD COLUMN IF NOT EXISTS text TEXT;
ALTER TABLE requirements ADD COLUMN IF NOT EXISTS target TEXT;
ALTER TABLE requirements ADD COLUMN IF NOT EXISTS category TEXT;
ALTER TABLE requirements ADD COLUMN IF NOT EXISTS obligation TEXT;
ALTER TABLE requirements ADD COLUMN IF NOT EXISTS quote TEXT;
ALTER TABLE requirements ADD COLUMN IF NOT EXISTS quote_score DOUBLE PRECISION;
ALTER TABLE requirements ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'auto';
ALTER TABLE requirements ADD COLUMN IF NOT EXISTS comment TEXT;
ALTER TABLE requirements ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ;

UPDATE requirements
SET text = COALESCE(text, description, title, 'Не указано')
WHERE text IS NULL;

CREATE TABLE IF NOT EXISTS open_questions (
  id BIGSERIAL PRIMARY KEY,
  text TEXT NOT NULL,
  context TEXT,
  sources TEXT,
  status TEXT NOT NULL DEFAULT 'open',
  answer TEXT,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS processes (
  id BIGSERIAL PRIMARY KEY,
  code TEXT UNIQUE,
  doc_id BIGINT REFERENCES documents(id) ON DELETE CASCADE,
  chunk_id BIGINT REFERENCES chunks(id) ON DELETE SET NULL,
  page INTEGER,
  name TEXT NOT NULL,
  trigger TEXT,
  initiator TEXT,
  amr_role TEXT,
  from_location TEXT,
  to_location TEXT,
  cargo TEXT,
  steps TEXT,
  quote TEXT,
  quote_score DOUBLE PRECISION,
  status TEXT NOT NULL DEFAULT 'auto',
  comment TEXT,
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS interfaces (
  id BIGSERIAL PRIMARY KEY,
  code TEXT UNIQUE,
  doc_id BIGINT REFERENCES documents(id) ON DELETE CASCADE,
  chunk_id BIGINT REFERENCES chunks(id) ON DELETE SET NULL,
  page INTEGER,
  source_system TEXT NOT NULL,
  target_system TEXT NOT NULL,
  message TEXT NOT NULL,
  protocol TEXT,
  trigger TEXT,
  quote TEXT,
  quote_score DOUBLE PRECISION,
  status TEXT NOT NULL DEFAULT 'auto',
  comment TEXT,
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS responsibilities (
  id BIGSERIAL PRIMARY KEY,
  code TEXT UNIQUE,
  doc_id BIGINT REFERENCES documents(id) ON DELETE CASCADE,
  chunk_id BIGINT REFERENCES chunks(id) ON DELETE SET NULL,
  page INTEGER,
  function TEXT NOT NULL,
  system TEXT NOT NULL,
  quote TEXT,
  quote_score DOUBLE PRECISION,
  status TEXT NOT NULL DEFAULT 'auto',
  comment TEXT,
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS conflicts (
  id BIGSERIAL PRIMARY KEY,
  key TEXT NOT NULL UNIQUE,
  kind TEXT NOT NULL,
  title TEXT NOT NULL,
  details TEXT,
  signature TEXT,
  status TEXT NOT NULL DEFAULT 'open',
  resolution TEXT,
  question_id BIGINT REFERENCES open_questions(id) ON DELETE SET NULL,
  updated_at TIMESTAMPTZ DEFAULT now()
);

ALTER TABLE extraction_log ADD COLUMN IF NOT EXISTS kind TEXT;
ALTER TABLE extraction_log ADD COLUMN IF NOT EXISTS found INTEGER;
ALTER TABLE extraction_log ADD COLUMN IF NOT EXISTS error TEXT;
UPDATE extraction_log SET kind = fact_kind WHERE kind IS NULL;

DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM information_schema.columns
    WHERE table_schema = current_schema()
      AND table_name = 'extraction_log'
      AND column_name = 'kind'
  ) THEN
    ALTER TABLE extraction_log DROP CONSTRAINT IF EXISTS extraction_log_pkey;
    ALTER TABLE extraction_log ADD PRIMARY KEY (chunk_id, kind);
  END IF;
END $$;

CREATE TABLE IF NOT EXISTS runs (
  id BIGSERIAL PRIMARY KEY,
  stage TEXT NOT NULL,
  started_at TIMESTAMPTZ DEFAULT now(),
  finished_at TIMESTAMPTZ,
  info TEXT
);

COMMIT;
