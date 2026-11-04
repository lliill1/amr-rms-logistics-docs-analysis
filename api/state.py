"""Общее состояние приложения: настройки, клиент LLM (со статусом в кэше на 30 секунд), задачи, подключения к базе."""
from __future__ import annotations

import time
from typing import Any

from core import db
from core.config import Settings, load_settings
from core.llm import OllamaClient

from .jobs import JobManager

STATUS_TTL = 30.0


class AppState:
    def __init__(self, settings: Settings | None = None, llm: Any = None) -> None:
        self.settings = settings or load_settings()
        self.llm = llm if llm is not None else OllamaClient.from_settings(self.settings)
        self.jobs = JobManager()
        self._status: dict | None = None
        self._status_at = 0.0
        db.connect(self.settings.db_file).close()   # создать базу и схему при старте

    def connect(self):
        """Новое подключение на запрос или задачу: SQLite в режиме WAL спокойно переносит несколько подключений."""
        return db.connect(self.settings.db_file)

    def llm_status(self, refresh: bool = False) -> dict:
        if refresh or self._status is None or time.time() - self._status_at > STATUS_TTL:
            fn = getattr(self.llm, "status", None)
            self._status = fn() if fn else {"ok": True, "models": [], "missing": [], "error": None}
            self._status_at = time.time()
        return self._status

    def llm_or_none(self):
        """Клиент LLM, если Ollama запущена и модели скачаны, иначе None (работаем без модели)."""
        return self.llm if self.llm_status()["ok"] else None
