"""Экран «Отчёт»: готовность разделов и выгрузка в Word и Excel."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from core import report
from core.db import one

from ..deps import get_conn, get_state
from ..schemas import DocxRequest
from ..state import AppState

router = APIRouter()

MIME = {".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}
TONES = {"готов": "green", "черновик": "orange", "нет данных": "gray", "следующая версия": "gray"}


def _n(conn, sql: str) -> int:
    return (one(conn, sql) or {"n": 0})["n"]


def _file_info(path: Path) -> dict:
    st = path.stat()
    return {"name": path.name, "size": st.st_size, "modified": st.st_mtime,
            "url": f"/api/report/download/{path.name}"}


@router.get("/report/summary")
def summary(state: AppState = Depends(get_state), conn=Depends(get_conn)):
    docs_total = _n(conn, "SELECT count(*) n FROM documents")
    docs_rel = _n(conn, "SELECT count(*) n FROM documents WHERE relevance > 0 AND is_current = 1")
    req_conf = _n(conn, "SELECT count(*) n FROM requirements WHERE status IN ('confirmed', 'edited')")
    req_auto = _n(conn, "SELECT count(*) n FROM requirements WHERE status = 'auto'")
    q_open = _n(conn, "SELECT count(*) n FROM open_questions WHERE status != 'closed'")

    sections = [
        ("1. Обзор архива и актуальные документы", f"{docs_total} файлов, {docs_rel} актуальных про AMR/RMS",
         "готов" if docs_total else "нет данных"),
        ("2. Общая схема логистики", "зоны, потоки, участки AMR", "следующая версия"),
        ("3. Карта процессов с участием AMR", "сценарии и последовательность операций", "следующая версия"),
        ("4. Схема взаимодействия систем", "EWM, ERP, MES, RMS, AMR — обмены", "следующая версия"),
        ("5. Сводная таблица функций и требований", f"подтверждено {req_conf}, ждут проверки {req_auto}",
         "готов" if req_conf and not req_auto else ("черновик" if req_conf or req_auto else "нет данных")),
        ("6. Противоречия и открытые вопросы", f"{q_open} открытых вопросов", "черновик" if q_open else "нет данных"),
    ]
    out_dir = state.settings.output_dir
    files = sorted((p for p in out_dir.glob("*") if p.suffix in MIME and p.is_file()),
                   key=lambda p: p.stat().st_mtime, reverse=True) if out_dir.exists() else []
    return {
        "sections": [{"title": t, "detail": d, "state": s, "tone": TONES[s]} for t, d, s in sections],
        "counts": {"documents": docs_total, "relevant": docs_rel, "confirmed": req_conf,
                   "unreviewed": req_auto, "open_questions": q_open},
        "can_export": docs_total > 0,
        "output_dir": str(out_dir),
        "files": [_file_info(p) for p in files],
    }


def _require_docs(conn) -> None:
    if not _n(conn, "SELECT count(*) n FROM documents"):
        raise HTTPException(409, "Архив пуст: сначала загрузите документы.")


@router.post("/report/docx")
def make_docx(body: DocxRequest, state: AppState = Depends(get_state), conn=Depends(get_conn)):
    _require_docs(conn)
    path = report.build_docx(conn, state.settings.output_dir, include_auto=body.include_auto, project=body.project)
    return _file_info(Path(path))


@router.post("/report/xlsx")
def make_xlsx(state: AppState = Depends(get_state), conn=Depends(get_conn)):
    _require_docs(conn)
    path = report.build_xlsx(conn, state.settings.output_dir)
    return _file_info(Path(path))


@router.get("/report/download/{filename}")
def download(filename: str, state: AppState = Depends(get_state)):
    # только файлы из папки отчётов; любые пути вида ../ отсекаются
    if Path(filename).name != filename or Path(filename).suffix not in MIME:
        raise HTTPException(400, "Недопустимое имя файла")
    path = state.settings.output_dir / filename
    if not path.is_file():
        raise HTTPException(404, "Файл не найден")
    return FileResponse(path, media_type=MIME[path.suffix], filename=filename)
