"""Экран 1. Архив и обработка: загрузка, ход конвейера, каталог документов с ручной правкой карточек."""
from pathlib import Path

import pandas as pd
import streamlit as st

from core import pipeline
from core.db import rows
from ui.common import get_conn, get_settings, llm_or_none, sidebar

sidebar()
settings, conn = get_settings(), get_conn()

st.title("Архив и обработка")

# ---------- загрузка ----------
with st.container(border=True):
    st.markdown("**Источник документов**")
    st.caption("Укажите папку или архив (.zip) на этом компьютере, например `D:\\Projects\\AMR\\archive`. "
               "Файлы никуда не копируются и не отправляются. Повторный запуск добавит только новые файлы.")
    c1, c2 = st.columns([4, 1])
    source = c1.text_input("Путь к папке или архиву", value=st.session_state.get("source", ""),
                           placeholder=r"D:\Projects\AMR\archive", label_visibility="collapsed")
    run = c2.button("Запустить обработку", type="primary", use_container_width=True)
    uploaded = st.file_uploader("…или загрузите zip-архив сюда", type=["zip"])

if run or uploaded:
    if uploaded:
        up_dir = settings.data_dir / "uploads"
        up_dir.mkdir(parents=True, exist_ok=True)
        src = up_dir / uploaded.name
        src.write_bytes(uploaded.getbuffer())
    else:
        src = Path(source.strip().strip('"'))
    if not src.exists():
        st.error(f"Путь не найден: {src}")
    else:
        st.session_state["source"] = str(src)
        bars: dict[str, object] = {}

        def progress(stage: str, done: int, total: int):
            if stage not in bars:
                bars[stage] = st.progress(0.0, text=stage)
            bars[stage].progress(done / max(total, 1), text=f"{stage}: {done} из {total}")

        with st.status("Обработка архива…", expanded=True) as status:
            stats = pipeline.run_all(conn, settings, src, llm=llm_or_none(), progress=progress)
            status.update(label="Обработка завершена", state="complete")
        st.success(f"Найдено файлов: {stats.found}. Новых: {stats.added}, повторов: {stats.duplicates}. "
                   f"Прочитано: {stats.parsed}, сканов: {stats.needs_ocr}, неподдерживаемых: {stats.unsupported}, "
                   f"ошибок: {stats.errors}. Устаревших редакций: {stats.superseded}.")
        for m in stats.messages:
            st.warning(m)
        if stats.skipped:
            with st.expander(f"Пропущено: {len(stats.skipped)}"):
                st.dataframe(pd.DataFrame(stats.skipped, columns=["Файл", "Причина"]), hide_index=True)

# ---------- сводка ----------
docs = rows(conn, """SELECT d.id, d.rel_path, d.title, d.doc_type, d.doc_date, d.revision, d.approval, d.side,
                            d.subsystems, d.relevance, d.is_current, d.status, d.error, d.card_source,
                            s.rel_path AS superseded_by_path,
                            (SELECT count(*) FROM chunks c WHERE c.doc_id = d.id) AS n_chunks,
                            (SELECT count(*) FROM chunks c WHERE c.doc_id = d.id AND c.embedding IS NOT NULL) AS n_vec
                     FROM documents d LEFT JOIN documents s ON s.id = d.superseded_by
                     ORDER BY d.relevance DESC, d.rel_path""")
if not docs:
    st.info("Архив ещё не загружен. Для пробы можно создать вымышленный демо-архив: "
            "`python scripts/make_demo_archive.py` и указать путь `data\\demo_archive`.")
    st.stop()

m = st.columns(5)
m[0].metric("Файлов", len(docs))
m[1].metric("Прочитано", sum(d["status"] == "parsed" for d in docs))
m[2].metric("Про AMR/RMS", sum((d["relevance"] or 0) > 0 and d["is_current"] for d in docs))
m[3].metric("Устаревших редакций", sum(not d["is_current"] for d in docs))
m[4].metric("Требуют ручной работы", sum(d["status"] in ("needs_ocr", "unsupported", "error") for d in docs))

n_chunks = sum(d["n_chunks"] for d in docs)
n_vec = sum(d["n_vec"] for d in docs)
if n_chunks and n_vec < n_chunks:
    c1, c2 = st.columns([3, 1])
    c1.caption(f"Смысловой поиск готов для {n_vec} из {n_chunks} фрагментов. Пока работает поиск по словам.")
    if c2.button("Досчитать эмбеддинги", disabled=llm_or_none() is None):
        bar = st.progress(0.0)
        pipeline.build_embeddings(conn, llm_or_none(), lambda _s, d, t: bar.progress(d / max(t, 1), text=f"{d} из {t}"))
        st.rerun()

# ---------- каталог ----------
st.subheader("Документы")
f1, f2, f3 = st.columns([3, 1, 1])
query = f1.text_input("Поиск по названию и пути", placeholder="ПР_EWM, протокол, маппинг…")
only_rel = f2.toggle("Только про AMR/RMS", value=False)
hide_old = f3.toggle("Скрыть устаревшие", value=False)

view = [d for d in docs
        if (not query or query.lower() in (d["rel_path"] + " " + (d["title"] or "")).lower())
        and (not only_rel or (d["relevance"] or 0) > 0)
        and (not hide_old or d["is_current"])]

df = pd.DataFrame(view)
if df.empty:
    st.caption("Под фильтр ничего не попало.")
    st.stop()
df["is_current"] = df["is_current"].astype(bool)
TYPES = ["Проектное решение", "Техническое задание", "Протокол", "Спецификация интерфейса", "Описание бизнес-процесса",
         "Схема", "Презентация", "Письмо", "Прочее"]
edited = st.data_editor(
    df[["id", "rel_path", "doc_type", "doc_date", "revision", "approval", "side", "subsystems", "relevance",
        "is_current", "status", "card_source"]],
    hide_index=True, use_container_width=True, height=min(600, 38 + 35 * len(df)),
    disabled=["id", "rel_path", "subsystems", "status", "card_source"],
    column_config={
        "id": st.column_config.NumberColumn("ID", width="small"),
        "rel_path": st.column_config.TextColumn("Документ", width="large"),
        "doc_type": st.column_config.SelectboxColumn("Тип", options=TYPES),
        "doc_date": st.column_config.TextColumn("Дата"),
        "revision": st.column_config.TextColumn("Ред."),
        "approval": st.column_config.SelectboxColumn("Статус", options=["утверждён", "проект", "не указано"]),
        "side": st.column_config.SelectboxColumn("Сторона", options=["заказчик", "подрядчик", "не указано"]),
        "subsystems": st.column_config.TextColumn("Подсистемы"),
        "relevance": st.column_config.NumberColumn("AMR/RMS", min_value=0, max_value=3, help="0 — не относится, 3 — ключевой"),
        "is_current": st.column_config.CheckboxColumn("Актуальный"),
        "status": st.column_config.TextColumn("Обработка"),
        "card_source": st.column_config.TextColumn("Карточка", help="manual — поправлена вручную, авто-разбор её не меняет"),
    },
    key="docs_editor",
)

changes = st.session_state.get("docs_editor", {}).get("edited_rows", {})
if changes and st.button("Сохранить правки карточек", type="primary"):
    cols = ["doc_type", "doc_date", "revision", "approval", "side", "relevance", "is_current"]
    for idx, row in edited.iterrows():
        orig = df.loc[idx]
        diff = {c: row[c] for c in cols if str(row[c]) != str(orig[c])}
        if diff:
            if "is_current" in diff:
                diff["is_current"] = int(bool(diff["is_current"]))
            sets = ", ".join(f"{k} = ?" for k in diff)
            conn.execute(f"UPDATE documents SET {sets}, card_source = 'manual' WHERE id = ?", (*diff.values(), int(row["id"])))
    conn.commit()
    st.success("Сохранено. Эти карточки больше не будут меняться автоматически.")
    st.rerun()

problems = [d for d in docs if d["status"] in ("needs_ocr", "unsupported", "error")]
if problems:
    with st.expander(f"Документы, требующие ручной обработки: {len(problems)}"):
        reason = {"needs_ocr": "скан без текста (OCR — в следующей версии)", "unsupported": "формат не поддерживается",
                  "error": "ошибка чтения"}
        st.dataframe(pd.DataFrame([{"Документ": d["rel_path"], "Причина": reason[d["status"]], "Подробности": d["error"]}
                                   for d in problems]), hide_index=True, use_container_width=True)
