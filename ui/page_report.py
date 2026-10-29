"""Экран 4. Отчёт: готовность разделов и выгрузка в Word и Excel."""
import streamlit as st

from core import report
from core.db import one
from ui.common import get_conn, get_settings, sidebar

sidebar()
settings, conn = get_settings(), get_conn()

st.title("Отчёт")
st.caption("Разделы соответствуют «Образу результата». Готовность считается по подтверждённым данным.")


def n(sql: str) -> int:
    return (one(conn, sql) or {"n": 0})["n"]


docs_total = n("SELECT count(*) n FROM documents")
docs_rel = n("SELECT count(*) n FROM documents WHERE relevance > 0 AND is_current = 1")
req_conf = n("SELECT count(*) n FROM requirements WHERE status IN ('confirmed', 'edited')")
req_auto = n("SELECT count(*) n FROM requirements WHERE status = 'auto'")
q_open = n("SELECT count(*) n FROM open_questions WHERE status != 'closed'")

SECTIONS = [
    ("1. Обзор архива и актуальные документы", f"{docs_total} файлов, {docs_rel} актуальных про AMR/RMS",
     "готов" if docs_total else "нет данных"),
    ("2. Общая схема логистики", "зоны, потоки, участки AMR", "следующая версия"),
    ("3. Карта процессов с участием AMR", "сценарии и последовательность операций", "следующая версия"),
    ("4. Схема взаимодействия систем", "EWM, ERP, MES, RMS, AMR — обмены", "следующая версия"),
    ("5. Сводная таблица функций и требований", f"подтверждено {req_conf}, ждут проверки {req_auto}",
     "готов" if req_conf and not req_auto else ("черновик" if req_conf or req_auto else "нет данных")),
    ("6. Противоречия и открытые вопросы", f"{q_open} открытых вопросов", "черновик" if q_open else "нет данных"),
]
COLORS = {"готов": "green", "черновик": "orange", "нет данных": "gray", "следующая версия": "gray"}

left, right = st.columns([3, 2], gap="large")
with left:
    for title, detail, state in SECTIONS:
        with st.container(border=True):
            a, b = st.columns([5, 2])
            a.markdown(f"**{title}**")
            a.caption(detail)
            b.markdown(f":{COLORS[state]}[{state}]")

with right:
    with st.container(border=True):
        st.markdown("**Параметры выгрузки**")
        include_auto = st.checkbox("Включить непроверенные требования (черновик)", value=False)
        project = st.text_input("Название проекта для титула", value="")
        out_dir = settings.output_dir
        st.caption(f"Файлы сохраняются в папку: `{out_dir}`")
        c1, c2 = st.columns(2)
        if c1.button("Сформировать Word", type="primary", use_container_width=True, disabled=not docs_total):
            st.session_state["docx"] = report.build_docx(conn, out_dir, include_auto=include_auto, project=project)
        if c2.button("Таблицы в Excel", use_container_width=True, disabled=not docs_total):
            st.session_state["xlsx"] = report.build_xlsx(conn, out_dir)
        for key, mime in (("docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
                          ("xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")):
            path = st.session_state.get(key)
            if path and path.exists():
                st.download_button(f"Скачать {path.name}", data=path.read_bytes(), file_name=path.name, mime=mime,
                                   use_container_width=True)
