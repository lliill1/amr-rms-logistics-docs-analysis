"""Служебное: здоровье, статус Ollama, справочники значений, фоновые задачи."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from .. import schemas
from ..deps import get_state
from ..state import AppState
from core.extract import CATEGORIES, OBLIGATIONS, TARGETS
from core.facts import KINDS
from core.vocab import load_vocab

router = APIRouter()


@router.get("/health")
def health():
    return {"ok": True}


@router.get("/status")
def status(refresh: bool = False, state: AppState = Depends(get_state)):
    s = state.llm_status(refresh=refresh)
    llm = state.llm
    kind = "ok" if s["ok"] else ("unreachable" if s.get("error") else "missing_models")
    return {
        "state": kind,
        "ok": s["ok"],
        "error": s.get("error"),
        "missing": s.get("missing", []),
        "pull_commands": [f"ollama pull {m}" for m in s.get("missing", [])] if kind == "missing_models" else [],
        "chat_model": getattr(llm, "chat_model", None),
        "embed_model": getattr(llm, "embed_model", None),
    }


@router.get("/meta")
def meta():
    """Справочники для выпадающих списков и подписей: единственный источник правды остаётся на сервере."""
    return {
        "doc_types": schemas.DOC_TYPES,
        "approvals": schemas.APPROVALS,
        "sides": schemas.SIDES,
        "targets": TARGETS,
        "categories": CATEGORIES,
        "obligations": OBLIGATIONS,
        "requirement_statuses": schemas.REQ_STATUS_LABELS,
        "question_statuses": schemas.QUESTION_STATUSES,
        # версия 2: справочник модели логистики (config/model.yaml) и виды извлекаемых фактов
        "systems": load_vocab().systems,
        "functions": [{"key": f.key, "title": f.title, "required": f.required} for f in load_vocab().functions],
        "amr_roles": load_vocab().amr_roles,
        "fact_kinds": [{"key": k.key, "title": k.title} for k in KINDS.values()],
    }


@router.get("/jobs/current")
def current_job(state: AppState = Depends(get_state)):
    """Активная (или последняя) задача: чтобы интерфейс после обновления страницы подхватил прогресс."""
    job = state.jobs.current()
    return job.to_dict() if job else None


@router.get("/jobs/{job_id}")
def get_job(job_id: str, state: AppState = Depends(get_state)):
    job = state.jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Задача не найдена")
    return job.to_dict()
