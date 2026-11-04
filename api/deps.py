from __future__ import annotations

from fastapi import HTTPException, Request

from .state import AppState


def get_state(request: Request) -> AppState:
    return request.app.state.core


def get_conn(request: Request):
    conn = request.app.state.core.connect()
    try:
        yield conn
    finally:
        conn.close()


def reject_nulls(data: dict, keys: tuple[str, ...]) -> None:
    """Для обязательных полей явный null недопустим (поле можно не передавать, но нельзя обнулить)."""
    bad = [k for k in keys if k in data and data[k] is None]
    if bad:
        raise HTTPException(422, f"Поле не может быть пустым: {', '.join(bad)}")
