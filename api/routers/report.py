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
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".zip": "application/zip"}
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
    # версия 2
    def facts(table: str, where: str = "1=1") -> tuple[int, int]:
        return (_n(conn, f"SELECT count(*) n FROM {table} WHERE status IN ('confirmed', 'edited') AND {where}"),
                _n(conn, f"SELECT count(*) n FROM {table} WHERE status = 'auto' AND {where}"))
    flow_c, flow_a = facts("processes", "coalesce(from_location, '') != '' AND coalesce(to_location, '') != ''")
    proc_c, proc_a = facts("processes", "amr_role != 'вручную'")
    if_c, if_a = facts("interfaces")
    rs_c, rs_a = facts("responsibilities")
    conflicts_open = _n(conn, "SELECT count(*) n FROM conflicts WHERE status = 'open'")

    def readiness(conf: int, auto: int) -> str:
        return "готов" if conf and not auto else ("черновик" if conf or auto else "нет данных")

    sections = [
        ("1. Обзор архива и актуальные документы", f"{docs_total} файлов, {docs_rel} актуальных про AMR/RMS",
         "готов" if docs_total else "нет данных"),
        ("2. Общая схема логистики", f"потоков: подтверждено {flow_c}, ждут проверки {flow_a}", readiness(flow_c, flow_a)),
        ("3. Карта процессов с участием AMR", f"процессов: подтверждено {proc_c}, ждут проверки {proc_a}",
         readiness(proc_c, proc_a)),
        ("4. Схема взаимодействия систем", f"обменов: подтверждено {if_c}, ждут проверки {if_a}", readiness(if_c, if_a)),
        ("5. Сводная таблица функций и требований",
         f"требований: подтверждено {req_conf}, ждут {req_auto}; ответственность: {rs_c + rs_a}",
         readiness(req_conf + rs_c, req_auto + rs_a)),
        ("6. Противоречия и открытые вопросы", f"{conflicts_open} неразобранных противоречий, {q_open} открытых вопросов",
         "черновик" if q_open or conflicts_open else "нет данных"),
    ]
    out_dir = state.settings.output_dir
    files = sorted((p for p in out_dir.glob("*") if p.suffix in MIME and p.is_file()),
                   key=lambda p: p.stat().st_mtime, reverse=True) if out_dir.exists() else []
    return {
        "sections": [{"title": t, "detail": d, "state": s, "tone": TONES[s]} for t, d, s in sections],
        "counts": {"documents": docs_total, "relevant": docs_rel, "confirmed": req_conf,
                   "unreviewed": req_auto, "open_questions": q_open,
                   "unreviewed_facts": sum(_n(conn, f"SELECT count(*) n FROM {t} WHERE status = 'auto'")
                                           for t in ("processes", "interfaces", "responsibilities")),
                   "open_conflicts": conflicts_open},
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


@router.post("/report/diagrams")
def make_diagrams(body: DocxRequest, state: AppState = Depends(get_state), conn=Depends(get_conn)):
    """Архив схем в редактируемом виде: draw.io, SVG, Mermaid, PNG."""
    _require_docs(conn)
    path = report.build_diagrams_zip(conn, state.settings.output_dir, include_auto=body.include_auto)
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
