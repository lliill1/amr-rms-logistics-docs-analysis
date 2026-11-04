"""Экран «Вопросы к архиву»: ответ модели строго по найденным фрагментам, со ссылками на документ и страницу."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from core.ask import ask, source_label

from ..deps import get_conn, get_state
from ..schemas import AskRequest
from ..state import AppState

router = APIRouter()


@router.post("/ask")
def ask_archive(body: AskRequest, state: AppState = Depends(get_state), conn=Depends(get_conn)):
    llm = state.llm_or_none()
    ans = ask(conn, state.settings, body.question.strip(), llm=llm, only_current=body.only_current)
    hits = [{
        "n": n, "chunk_id": h.chunk_id, "doc_id": h.doc_id, "label": source_label(h), "rel_path": h.rel_path,
        "title": h.title, "section": h.section, "page_from": h.page_from, "page_to": h.page_to, "text": h.text,
        "is_current": bool(h.is_current), "approval": h.approval, "side": h.side, "score": h.score,
    } for n, h in enumerate(ans.hits, start=1)]
    return {
        "question": body.question.strip(),
        "text": ans.text,
        "error": ans.error,
        "model_available": llm is not None,
        "hits": hits,
        # готовая строка для «Добавить в открытые вопросы» (первые три источника)
        "sources": "; ".join(h["label"] for h in hits[:3]),
    }
