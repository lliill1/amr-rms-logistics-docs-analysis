"""Версия 2. Экран «Логистика и системы» и противоречия:
процессы, обмены, ответственность (ревью как у требований), схемы в SVG, матрица, противоречия и пробелы."""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

from core import consolidate, diagrams
from core.db import one, rows
from core.vocab import load_vocab

from .. import schemas
from ..deps import get_conn, reject_nulls

router = APIRouter()

ReqStatus = schemas.ReqStatus


# ───────── модели запросов ─────────

class Step(BaseModel):
    actor: str = Field(min_length=1)
    action: str = Field(min_length=1)


class ProcessPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1)
    trigger: str | None = None
    initiator: str | None = None
    amr_role: str | None = None
    from_location: str | None = None
    to_location: str | None = None
    cargo: str | None = None
    steps: list[Step] | None = None
    comment: str | None = None
    status: ReqStatus | None = None


class InterfacePatch(BaseModel):
    source_system: str | None = None
    target_system: str | None = None
    message: str | None = Field(default=None, min_length=1)
    protocol: str | None = None
    trigger: str | None = None
    comment: str | None = None
    status: ReqStatus | None = None


class ResponsibilityPatch(BaseModel):
    function: str | None = None
    system: str | None = None
    comment: str | None = None
    status: ReqStatus | None = None


class ConfirmRequest(BaseModel):
    ids: list[int]


class ResolveRequest(BaseModel):
    value: str = Field(min_length=1)


# ───────── общий ревью для трёх видов фактов ─────────

TABLES: dict[str, dict[str, Any]] = {
    "processes": {
        "sql": "SELECT p.*, d.rel_path, d.is_current, c.text AS chunk_text FROM processes p "
               "JOIN documents d ON d.id = p.doc_id LEFT JOIN chunks c ON c.id = p.chunk_id",
        "alias": "p", "order": "p.code", "search": ("name", "trigger", "from_location", "to_location", "cargo", "quote"),
        "required": ("name", "status"), "model": ProcessPatch, "title": "Процесс",
    },
    "interfaces": {
        "sql": "SELECT i.*, d.rel_path, d.is_current, c.text AS chunk_text FROM interfaces i "
               "JOIN documents d ON d.id = i.doc_id LEFT JOIN chunks c ON c.id = i.chunk_id",
        "alias": "i", "order": "i.source_system, i.target_system, i.code",
        "search": ("source_system", "target_system", "message", "protocol", "trigger", "quote"),
        "required": ("source_system", "target_system", "message", "status"), "model": InterfacePatch, "title": "Обмен",
    },
    "responsibilities": {
        "sql": "SELECT r.*, d.rel_path, d.is_current, c.text AS chunk_text FROM responsibilities r "
               "JOIN documents d ON d.id = r.doc_id LEFT JOIN chunks c ON c.id = r.chunk_id",
        "alias": "r", "order": "r.function, r.system, r.code", "search": ("function", "system", "quote"),
        "required": ("function", "system", "status"), "model": ResponsibilityPatch, "title": "Факт",
    },
}


def _out(table: str, r: dict, detail: bool = False) -> dict:
    r["status_label"] = schemas.REQ_STATUS_LABELS.get(r["status"], r["status"])
    r["source"] = r["rel_path"] + (f", с. {int(r['page'])}" if r.get("page") is not None else "")
    r["is_current"] = bool(r.get("is_current"))
    if table == "processes":
        r["steps"] = diagrams.process_steps(r)
    if table == "responsibilities":
        f = load_vocab().function_by_key(r["function"])
        r["function_title"] = f.title if f else r["function"]
    if not detail:
        r.pop("chunk_text", None)
    return r


def _validate(table: str, data: dict) -> dict:
    v = load_vocab()
    for key in ("initiator", "source_system", "target_system", "system"):
        if data.get(key) is not None and data[key] not in v.systems:
            raise HTTPException(422, f"{key}: допустимые значения — {', '.join(v.systems)}")
    if data.get("amr_role") is not None and data["amr_role"] not in v.amr_roles:
        raise HTTPException(422, f"amr_role: допустимые значения — {', '.join(v.amr_roles)}")
    if data.get("function") is not None:
        key = v.function_key(data["function"])
        if not key:
            raise HTTPException(422, "function: неизвестная функция (см. config/model.yaml)")
        data["function"] = key
    if "steps" in data and data["steps"] is not None:
        for st in data["steps"]:
            if st["actor"] not in v.systems:
                raise HTTPException(422, f"steps.actor: допустимые значения — {', '.join(v.systems)}")
        data["steps"] = json.dumps(data["steps"], ensure_ascii=False)
    return data


def _list(table: str, conn, status: list[str], q: str) -> dict:
    cfg = TABLES[table]
    items = rows(conn, f"{cfg['sql']} ORDER BY {cfg['order']}")
    by_status: dict[str, int] = {}
    for r in items:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
    ql = q.lower().strip()
    view = [r for r in items if (not status or r["status"] in status)
            and (not ql or ql in " ".join(str(r.get(k) or "") for k in cfg["search"]).lower())]
    return {"total": len(items), "by_status": by_status, "items": [_out(table, r) for r in view]}


def _get(table: str, conn, item_id: int) -> dict:
    cfg = TABLES[table]
    r = one(conn, f"{cfg['sql']} WHERE {cfg['alias']}.id = ?", (item_id,))
    if not r:
        raise HTTPException(404, f"{cfg['title']} не найден")
    return _out(table, r, detail=True)


def _patch(table: str, conn, item_id: int, patch: BaseModel) -> dict:
    cfg = TABLES[table]
    orig = one(conn, f"SELECT * FROM {table} WHERE id = ?", (item_id,))
    if not orig:
        raise HTTPException(404, f"{cfg['title']} не найден")
    data = patch.model_dump(exclude_unset=True)
    reject_nulls(data, cfg["required"])
    data = _validate(table, data)
    fields = {k: v for k, v in data.items() if k != "status" and str(v if v is not None else "") != str(orig[k] if orig[k] is not None else "")}
    new_status = data.get("status", orig["status"])
    if fields and set(fields) - {"comment"} and new_status == "auto":
        new_status = "edited"          # правка содержания — это уже не «сырой» результат модели
    if new_status != orig["status"]:
        fields["status"] = new_status
    if fields:
        sets = ", ".join(f"{k} = ?" for k in fields)
        conn.execute(f"UPDATE {table} SET {sets}, updated_at = datetime('now') WHERE id = ?", (*fields.values(), item_id))
        conn.commit()
    return _get(table, conn, item_id)


def _confirm(table: str, conn, ids: list[int]) -> dict:
    n = 0
    for i in ids:
        n += conn.execute(f"UPDATE {table} SET status = 'confirmed', updated_at = datetime('now') "
                          "WHERE id = ? AND status = 'auto'", (i,)).rowcount
    conn.commit()
    return {"confirmed": n}


def _register(table: str, patch_model: type[BaseModel]) -> None:
    """Четыре маршрута на вид факта: список, карточка, правка, массовое подтверждение."""

    def list_items(status: list[str] = Query(default=[]), q: str = "", conn=Depends(get_conn)):
        return _list(table, conn, status, q)

    def confirm(body: ConfirmRequest, conn=Depends(get_conn)):
        return _confirm(table, conn, body.ids)

    def get_item(item_id: int, conn=Depends(get_conn)):
        return _get(table, conn, item_id)

    def patch_item(item_id: int, patch, conn=Depends(get_conn)):
        return _patch(table, conn, item_id, patch)

    # модуль использует отложенные аннотации, поэтому тип тела запроса задаём явно — иначе FastAPI его не увидит
    for fn in (list_items, confirm, get_item, patch_item):
        fn.__annotations__ = {k: v for k, v in fn.__annotations__.items() if k != "return"}
    list_items.__annotations__.update(status=list[str], q=str)
    confirm.__annotations__["body"] = ConfirmRequest
    get_item.__annotations__["item_id"] = int
    patch_item.__annotations__.update(item_id=int, patch=patch_model)

    router.add_api_route(f"/{table}", list_items, methods=["GET"], name=f"list_{table}")
    router.add_api_route(f"/{table}/confirm", confirm, methods=["POST"], name=f"confirm_{table}")
    router.add_api_route(f"/{table}/{{item_id}}", get_item, methods=["GET"], name=f"get_{table}")
    router.add_api_route(f"/{table}/{{item_id}}", patch_item, methods=["PATCH"], name=f"patch_{table}")


_register("processes", ProcessPatch)
_register("interfaces", InterfacePatch)
_register("responsibilities", ResponsibilityPatch)


# ───────── матрица и схемы ─────────

@router.get("/model/matrix")
def matrix(include_auto: bool = True, conn=Depends(get_conn)):
    return consolidate.responsibility_matrix(conn, include_auto)


@router.get("/model/overview")
def overview(conn=Depends(get_conn)):
    """Счётчики для вкладок экрана «Логистика и системы» и раздела отчёта."""
    def counts(table: str) -> dict:
        out = {r["status"]: r["n"] for r in rows(conn, f"SELECT status, count(*) n FROM {table} GROUP BY status")}
        return {"total": sum(out.values()), "by_status": out}
    procs = rows(conn, "SELECT amr_role, count(*) n FROM processes WHERE status != 'rejected' GROUP BY amr_role")
    return {"processes": counts("processes"), "interfaces": counts("interfaces"),
            "responsibilities": counts("responsibilities"),
            "amr_roles": {r["amr_role"]: r["n"] for r in procs},
            "open_conflicts": (one(conn, "SELECT count(*) n FROM conflicts WHERE status = 'open'") or {"n": 0})["n"]}


def _svg(d: diagrams.Diagram, uid: str) -> Response:
    if d.empty:
        raise HTTPException(404, d.note or "Нет данных для схемы")
    return Response(diagrams.to_svg(d, uid=uid), media_type="image/svg+xml",
                    headers={"Cache-Control": "no-store", "X-Diagram-Note": _ascii_note(d.note)})


def _ascii_note(note: str | None) -> str:
    # заголовки HTTP — только latin-1; примечание передаём в процентной кодировке
    from urllib.parse import quote
    return quote(note or "")


@router.get("/diagrams/systems.svg")
def diagram_systems(include_auto: bool = True, conn=Depends(get_conn)):
    return _svg(diagrams.systems_diagram(conn, include_auto), "sys")


@router.get("/diagrams/logistics.svg")
def diagram_logistics(include_auto: bool = True, conn=Depends(get_conn)):
    return _svg(diagrams.logistics_diagram(conn, include_auto), "log")


@router.get("/diagrams/process/{process_id}.svg")
def diagram_process(process_id: int, conn=Depends(get_conn)):
    p = one(conn, "SELECT * FROM processes WHERE id = ?", (process_id,))
    if not p:
        raise HTTPException(404, "Процесс не найден")
    return _svg(diagrams.process_diagram(p), f"pr{process_id}")


@router.get("/diagrams/mermaid")
def diagram_mermaid(include_auto: bool = True, conn=Depends(get_conn)):
    return {d.key: diagrams.to_mermaid(d) for d in diagrams.all_diagrams(conn, include_auto) if not d.empty}


# ───────── противоречия и пробелы ─────────

@router.get("/conflicts")
def list_conflicts(conn=Depends(get_conn)):
    items = consolidate.refresh(conn)
    counts: dict[str, int] = {}
    for c in items:
        if c["status"] == "open":
            counts[c["kind"]] = counts.get(c["kind"], 0) + 1
    return {"items": items, "open": sum(counts.values()), "open_by_kind": counts,
            "kind_labels": consolidate.KIND_LABELS, "status_labels": consolidate.STATUS_LABELS}


def _conflict_or_404(conn, conflict_id: int) -> dict:
    c = consolidate.get_conflict(conn, conflict_id)
    if not c:
        raise HTTPException(404, "Противоречие не найдено")
    return c


@router.post("/conflicts/{conflict_id}/resolve")
def resolve_conflict(conflict_id: int, body: ResolveRequest, conn=Depends(get_conn)):
    _conflict_or_404(conn, conflict_id)
    try:
        return consolidate.resolve(conn, conflict_id, body.value)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


@router.post("/conflicts/{conflict_id}/question")
def conflict_to_question(conflict_id: int, conn=Depends(get_conn)):
    _conflict_or_404(conn, conflict_id)
    return consolidate.to_question(conn, conflict_id)


@router.post("/conflicts/{conflict_id}/dismiss")
def dismiss_conflict(conflict_id: int, conn=Depends(get_conn)):
    _conflict_or_404(conn, conflict_id)
    return consolidate.set_status(conn, conflict_id, "dismissed")


@router.post("/conflicts/{conflict_id}/reopen")
def reopen_conflict(conflict_id: int, conn=Depends(get_conn)):
    _conflict_or_404(conn, conflict_id)
    return consolidate.set_status(conn, conflict_id, "open")
