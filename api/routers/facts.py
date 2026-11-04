"""Экран «Факты и ревью»: извлечение требований моделью, подтверждение аналитиком, открытые вопросы."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from core.db import one, rows
from core.extract import candidate_chunks, extract_requirements

from .. import schemas
from ..deps import get_conn, get_state, reject_nulls
from ..schemas import ConfirmRequest, ExtractionRequest, QuestionCreate, QuestionPatch, RequirementPatch
from ..state import AppState

router = APIRouter()

REQ_SQL = """SELECT r.id, r.code, r.text, r.target, r.category, r.obligation, r.status, r.comment,
                    r.quote, r.quote_score, r.page, r.doc_id, d.rel_path, c.text AS chunk_text
             FROM requirements r JOIN documents d ON d.id = r.doc_id
             LEFT JOIN chunks c ON c.id = r.chunk_id"""


def _req_out(r: dict, detail: bool = False) -> dict:
    r["status_label"] = schemas.REQ_STATUS_LABELS.get(r["status"], r["status"])
    r["source"] = r["rel_path"] + (f", с. {int(r['page'])}" if r["page"] is not None else "")
    if not detail:
        r.pop("chunk_text", None)
        r.pop("quote", None)
    return r


# ---------- извлечение ----------

@router.get("/extraction/pending")
def pending(state: AppState = Depends(get_state), conn=Depends(get_conn)):
    items = candidate_chunks(conn, int(state.settings.get("extraction.min_relevance", 1)))
    return {"pending": len(items), "model_available": state.llm_or_none() is not None}


@router.post("/extraction/run", status_code=202)
def run_extraction(body: ExtractionRequest, state: AppState = Depends(get_state), conn=Depends(get_conn)):
    llm = state.llm_or_none()
    if llm is None:
        raise HTTPException(409, "Для извлечения нужна запущенная Ollama с моделью.")
    items = candidate_chunks(conn, int(state.settings.get("extraction.min_relevance", 1)))
    if not items:
        raise HTTPException(409, "Необработанных фрагментов про AMR/RMS нет.")
    limit = body.limit
    doc_ids = sorted({c["doc_id"] for c in items[:limit]})

    def run(progress):
        job_conn = state.connect()
        counter = {"n": 0}

        def on_progress(done: int, total: int) -> None:
            counter["n"] = done
            shown = min(total, limit)
            progress("Извлечение требований", min(done, shown), shown)

        try:
            return extract_requirements(job_conn, state.settings, llm, doc_ids=doc_ids, progress=on_progress,
                                        stop=lambda: counter["n"] >= limit)
        finally:
            job_conn.close()

    return state.jobs.start("extraction", run).to_dict()


# ---------- требования ----------

@router.get("/requirements")
def list_requirements(target: list[str] = Query(default=[]), status: list[str] = Query(default=[]),
                      q: str = "", conn=Depends(get_conn)):
    reqs = rows(conn, REQ_SQL + " ORDER BY r.code")
    by_status: dict[str, int] = {}
    for r in reqs:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
    q = q.lower()
    view = [r for r in reqs
            if (not target or r["target"] in target)
            and (not status or r["status"] in status)
            and (not q or q in (r["text"] + " " + (r["quote"] or "")).lower())]
    return {"total": len(reqs), "by_status": by_status, "items": [_req_out(r) for r in view]}


@router.post("/requirements/confirm")
def confirm_requirements(body: ConfirmRequest, conn=Depends(get_conn)):
    """Подтвердить переданные требования; затрагивает только те, что ещё в статусе «найдено моделью»."""
    n = 0
    for i in body.ids:
        n += conn.execute("UPDATE requirements SET status = 'confirmed', updated_at = datetime('now') "
                          "WHERE id = ? AND status = 'auto'", (i,)).rowcount
    conn.commit()
    return {"confirmed": n}


@router.get("/requirements/{req_id}")
def get_requirement(req_id: int, conn=Depends(get_conn)):
    r = one(conn, REQ_SQL + " WHERE r.id = ?", (req_id,))
    if not r:
        raise HTTPException(404, "Требование не найдено")
    return _req_out(r, detail=True)


@router.patch("/requirements/{req_id}")
def patch_requirement(req_id: int, patch: RequirementPatch, conn=Depends(get_conn)):
    orig = one(conn, "SELECT * FROM requirements WHERE id = ?", (req_id,))
    if not orig:
        raise HTTPException(404, "Требование не найдено")
    data = patch.model_dump(exclude_unset=True)
    reject_nulls(data, ("text", "target", "category", "obligation", "status"))
    fields = {c: data[c] for c in ("text", "target", "category", "obligation", "comment")
              if c in data and str(data[c]) != str(orig[c])}
    new_status = data.get("status", orig["status"])
    if "text" in fields and new_status == "auto":     # правка текста — это уже не «сырой» результат модели
        new_status = "edited"
    if new_status != orig["status"]:
        fields["status"] = new_status
    if fields:
        sets = ", ".join(f"{k} = ?" for k in fields)
        conn.execute(f"UPDATE requirements SET {sets}, updated_at = datetime('now') WHERE id = ?", (*fields.values(), req_id))
        conn.commit()
    return _req_out(one(conn, REQ_SQL + " WHERE r.id = ?", (req_id,)), detail=True)


# ---------- открытые вопросы ----------

@router.get("/open-questions")
def list_questions(conn=Depends(get_conn)):
    return rows(conn, "SELECT id, text, context, sources, status, answer, created_at FROM open_questions ORDER BY id")


@router.post("/open-questions", status_code=201)
def create_question(body: QuestionCreate, conn=Depends(get_conn)):
    cur = conn.execute("INSERT INTO open_questions(text, context, sources, status, answer) VALUES (?, ?, ?, ?, ?)",
                       (body.text, (body.context or "")[:1000], body.sources, body.status, body.answer))
    conn.commit()
    return one(conn, "SELECT * FROM open_questions WHERE id = ?", (cur.lastrowid,))


@router.patch("/open-questions/{q_id}")
def patch_question(q_id: int, patch: QuestionPatch, conn=Depends(get_conn)):
    if not one(conn, "SELECT id FROM open_questions WHERE id = ?", (q_id,)):
        raise HTTPException(404, "Вопрос не найден")
    data = patch.model_dump(exclude_unset=True)
    reject_nulls(data, ("text", "status"))
    if data:
        sets = ", ".join(f"{k} = ?" for k in data)
        conn.execute(f"UPDATE open_questions SET {sets} WHERE id = ?", (*data.values(), q_id))
        conn.commit()
    return one(conn, "SELECT * FROM open_questions WHERE id = ?", (q_id,))


@router.delete("/open-questions/{q_id}", status_code=204)
def delete_question(q_id: int, conn=Depends(get_conn)):
    if not conn.execute("DELETE FROM open_questions WHERE id = ?", (q_id,)).rowcount:
        raise HTTPException(404, "Вопрос не найден")
    conn.commit()
