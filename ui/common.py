"""Общие ресурсы интерфейса: настройки, подключение к базе, клиент LLM, боковая панель."""
from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import db  # noqa: E402
from core.config import load_settings  # noqa: E402
from core.llm import OllamaClient  # noqa: E402


@st.cache_resource
def get_settings():
    return load_settings()


@st.cache_resource
def get_conn():
    return db.connect(get_settings().db_file)


@st.cache_resource
def get_llm() -> OllamaClient:
    return OllamaClient.from_settings(get_settings())


@st.cache_data(ttl=30)
def llm_status() -> dict:
    return get_llm().status()


def llm_or_none():
    """Клиент LLM, если Ollama запущена и модели скачаны, иначе None (работаем без модели)."""
    s = llm_status()
    return get_llm() if s["ok"] else None


def sidebar() -> None:
    s = llm_status()
    llm = get_llm()
    with st.sidebar:
        st.markdown("**DocAnalyzer** · AMR/RMS")
        if s["ok"]:
            st.success(f"Ollama работает\n\nLLM: `{llm.chat_model}`\n\nЭмбеддинги: `{llm.embed_model}`")
        elif s["error"]:
            st.error("Ollama не отвечает. Запустите Ollama (значок в трее Windows) и обновите страницу.")
            st.caption("Без модели работают загрузка, каталог и поиск по словам.")
        else:
            st.warning("Не скачаны модели: " + ", ".join(f"`{m}`" for m in s["missing"]))
            st.code("\n".join(f"ollama pull {m}" for m in s["missing"]), language="bash")
        st.caption("Документы обрабатываются только на этом компьютере.")
        if st.button("Проверить снова", use_container_width=True):
            llm_status.clear()
            st.rerun()


def source_label(rel_path: str, page_from=None, page_to=None) -> str:
    if page_from and page_to and page_to != page_from:
        return f"{rel_path}, с. {page_from}–{page_to}"
    if page_from:
        return f"{rel_path}, с. {page_from}"
    return rel_path
