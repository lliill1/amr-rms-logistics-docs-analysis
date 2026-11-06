"""Допустимые значения и модели запросов."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from core.extract import CATEGORIES, OBLIGATIONS, TARGETS

DOC_TYPES = ["Проектное решение", "Техническое задание", "Протокол", "Спецификация интерфейса",
             "Описание бизнес-процесса", "Схема", "Презентация", "Письмо", "Прочее"]
APPROVALS = ["утверждён", "проект", "не указано"]
SIDES = ["заказчик", "подрядчик", "не указано"]
REQ_STATUS_LABELS = {"auto": "найдено моделью", "confirmed": "подтверждено", "edited": "исправлено", "rejected": "отклонено"}
QUESTION_STATUSES = ["open", "sent", "answered", "closed"]

DocType = Literal[tuple(DOC_TYPES)]  # type: ignore[valid-type]
Approval = Literal[tuple(APPROVALS)]  # type: ignore[valid-type]
Side = Literal[tuple(SIDES)]  # type: ignore[valid-type]
Target = Literal[tuple(TARGETS)]  # type: ignore[valid-type]
Category = Literal[tuple(CATEGORIES)]  # type: ignore[valid-type]
Obligation = Literal[tuple(OBLIGATIONS)]  # type: ignore[valid-type]
ReqStatus = Literal["auto", "confirmed", "edited", "rejected"]
QuestionStatus = Literal["open", "sent", "answered", "closed"]


class ProcessRequest(BaseModel):
    path: str = Field(min_length=1, description="Путь к папке или zip-архиву на этом компьютере")


class DocumentPatch(BaseModel):
    doc_type: DocType | None = None
    doc_date: str | None = None
    revision: str | None = None
    approval: Approval | None = None
    side: Side | None = None
    relevance: int | None = Field(default=None, ge=0, le=3)
    is_current: bool | None = None


class AskRequest(BaseModel):
    question: str = Field(min_length=1)
    only_current: bool = True


class ExtractionRequest(BaseModel):
    limit: int = Field(default=20, ge=1)
    # версия 2: какие виды фактов извлекать (requirements, processes, integration); пусто — все
    kinds: list[Literal["requirements", "processes", "integration"]] = Field(default_factory=list)


class RequirementPatch(BaseModel):
    text: str | None = Field(default=None, min_length=1)
    target: Target | None = None
    category: Category | None = None
    obligation: Obligation | None = None
    comment: str | None = None
    status: ReqStatus | None = None


class ConfirmRequest(BaseModel):
    ids: list[int]


class QuestionCreate(BaseModel):
    text: str = Field(min_length=1)
    context: str | None = None
    sources: str | None = None
    status: QuestionStatus = "open"
    answer: str | None = None


class QuestionPatch(BaseModel):
    text: str | None = Field(default=None, min_length=1)
    context: str | None = None
    sources: str | None = None
    status: QuestionStatus | None = None
    answer: str | None = None


class DocxRequest(BaseModel):
    include_auto: bool = False
    project: str = ""
