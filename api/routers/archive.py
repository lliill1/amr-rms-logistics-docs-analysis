"""Экран «Архив и обработка»: загрузка, ход конвейера, каталог документов с ручной правкой карточек."""
from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from core import pipeline
from core.db import one, rows

from .. import schemas
from ..deps import get_conn, get_state, reject_nulls
from ..schemas import DocumentPatch, ProcessRequest
from ..state import AppState

router = APIRouter()

PROBLEM_STATUSES = ("needs_ocr", "unsupported", "error")
PROBLEM_REASONS = {"needs_ocr": "скан без текста (OCR — в следующей версии)",
                   "unsupported": "формат не поддерживается", "error": "ошибка чтения"}

DOC_SQL = """SELECT d.id, d.rel_path, d.title, d.doc_type, d.doc_date, d.revision, d.approval, d.side,
                    d.subsystems, d.relevance, d.is_current, d.status, d.error, d.card_source,
                    s.rel_path AS superseded_by_path,
                    (SELECT count(*) FROM chunks c WHERE c.doc_id = d.id) AS n_chunks,
                    (SELECT count(*) FROM chunks c WHERE c.doc_id = d.id AND c.embedding IS NOT NULL) AS n_vec
             FROM documents d LEFT JOIN documents s ON s.id = d.superseded_by"""


def _doc_out(d: dict) -> dict:
    d["is_current"] = bool(d["is_current"])
    return d


def _stats_out(stats, source: str) -> dict:
    out = dict(stats.__dict__)
    out["skipped"] = [{"file": f, "reason": r} for f, r in stats.skipped]
    out["source"] = source
    return out


def _start_processing(state: AppState, src: Path) -> dict:
    llm = state.llm_or_none()

    def run(progress):
        conn = state.connect()
        try:
            stats = pipeline.run_all(conn, state.settings, src, llm=llm, progress=progress)
        finally:
            conn.close()
        return _stats_out(stats, str(src))

    return state.jobs.start("process", run).to_dict()


@router.post("/archive/process", status_code=202)
def process_path(body: ProcessRequest, state: AppState = Depends(get_state)):
    src = Path(body.path.strip().strip('"'))
    if not src.exists():
        raise HTTPException(404, f"Путь не найден: {src}")
    return _start_processing(state, src)


@router.post("/archive/upload", status_code=202)
def upload_zip(file: UploadFile = File(...), state: AppState = Depends(get_state)):
    name = Path(file.filename or "").name
    if not name.lower().endswith(".zip"):
        raise HTTPException(400, "Нужен файл .zip")
    up_dir = state.settings.data_dir / "uploads"
    up_dir.mkdir(parents=True, exist_ok=True)
    src = up_dir / name
    with src.open("wb") as out:
        shutil.copyfileobj(file.file, out)
    return _start_processing(state, src)


@router.post("/archive/embeddings", status_code=202)
def build_embeddings(state: AppState = Depends(get_state)):
    llm = state.llm_or_none()
    if llm is None:
        raise HTTPException(409, "Для смыслового поиска нужна запущенная Ollama с моделью эмбеддингов.")

    def run(progress):
        conn = state.connect()
        try:
            n = pipeline.build_embeddings(conn, llm, progress)
        finally:
            conn.close()
        return {"embedded": n}

    return state.jobs.start("embeddings", run).to_dict()


@router.get("/archive/summary")
def summary(conn=Depends(get_conn)):
    docs = rows(conn, DOC_SQL)
    n_chunks = sum(d["n_chunks"] for d in docs)
    n_vec = sum(d["n_vec"] for d in docs)
    return {
        "files": len(docs),
        "parsed": sum(d["status"] == "parsed" for d in docs),
        "relevant": sum((d["relevance"] or 0) > 0 and bool(d["is_current"]) for d in docs),
        "outdated": sum(not d["is_current"] for d in docs),
        "need_manual": sum(d["status"] in PROBLEM_STATUSES for d in docs),
        "chunks": n_chunks,
        "vectors": n_vec,
        "embeddings_pending": bool(n_chunks and n_vec < n_chunks),
        "problems": [{"id": d["id"], "rel_path": d["rel_path"], "status": d["status"],
                      "reason": PROBLEM_REASONS[d["status"]], "details": d["error"]}
                     for d in docs if d["status"] in PROBLEM_STATUSES],
    }


@router.get("/documents")
def list_documents(query: str = "", only_relevant: bool = False, hide_outdated: bool = False,
                   conn=Depends(get_conn)):
    docs = rows(conn, DOC_SQL + " ORDER BY d.relevance DESC, d.rel_path")
    q = query.lower()
    view = [d for d in docs
            if (not q or q in (d["rel_path"] + " " + (d["title"] or "")).lower())
            and (not only_relevant or (d["relevance"] or 0) > 0)
            and (not hide_outdated or d["is_current"])]
    return {"total": len(docs), "items": [_doc_out(d) for d in view]}


@router.patch("/documents/{doc_id}")
def patch_document(doc_id: int, patch: DocumentPatch, conn=Depends(get_conn)):
    orig = one(conn, "SELECT * FROM documents WHERE id = ?", (doc_id,))
    if not orig:
        raise HTTPException(404, "Документ не найден")
    data = patch.model_dump(exclude_unset=True)
    reject_nulls(data, ("doc_type", "approval", "side", "relevance", "is_current"))
    if "is_current" in data:
        data["is_current"] = int(bool(data["is_current"]))
    diff = {k: v for k, v in data.items() if str(v) != str(orig[k])}
    if diff:
        sets = ", ".join(f"{k} = ?" for k in diff)
        conn.execute(f"UPDATE documents SET {sets}, card_source = 'manual' WHERE id = ?", (*diff.values(), doc_id))
        conn.commit()
    return _doc_out(rows(conn, DOC_SQL + " WHERE d.id = ?", (doc_id,))[0])
