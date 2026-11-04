"""Фоновые задачи (обработка архива, эмбеддинги, извлечение требований) с отслеживанием прогресса.

Одновременно выполняется одна задача: все они пишут в одну SQLite-базу, а приложение однопользовательское.
"""
from __future__ import annotations

import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable


class JobBusy(RuntimeError):
    pass


@dataclass
class Job:
    id: str
    kind: str
    state: str = "running"                       # running | done | error
    stages: dict[str, dict[str, int]] = field(default_factory=dict)
    result: dict[str, Any] | None = None
    error: str | None = None
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id, "kind": self.kind, "state": self.state,
            "stages": [{"name": n, **v} for n, v in self.stages.items()],
            "result": self.result, "error": self.error,
            "started_at": self.started_at, "finished_at": self.finished_at,
        }


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._current: str | None = None

    def start(self, kind: str, fn: Callable[[Callable[[str, int, int], None]], dict]) -> Job:
        """fn получает функцию progress(этап, сделано, всего) и возвращает словарь с итогом."""
        with self._lock:
            cur = self._jobs.get(self._current) if self._current else None
            if cur and cur.state == "running":
                raise JobBusy(f"Уже выполняется задача «{cur.kind}». Дождитесь её завершения.")
            job = Job(id=uuid.uuid4().hex[:12], kind=kind)
            self._jobs[job.id] = job
            self._current = job.id

        def progress(stage: str, done: int, total: int) -> None:
            job.stages[stage] = {"done": int(done), "total": int(total)}

        def run() -> None:
            try:
                job.result = fn(progress)
                job.state = "done"
            except Exception as e:  # noqa: BLE001 — любая ошибка должна дойти до интерфейса, а не потеряться в потоке
                job.error = f"{type(e).__name__}: {e}"
                job.state = "error"
                traceback.print_exc()
            finally:
                job.finished_at = time.time()

        threading.Thread(target=run, daemon=True, name=f"job-{job.id}").start()
        return job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def current(self) -> Job | None:
        return self._jobs.get(self._current) if self._current else None
