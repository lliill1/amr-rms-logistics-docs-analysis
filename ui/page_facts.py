"""Экран 3. Факты и ревью: извлечение требований моделью и подтверждение аналитиком; открытые вопросы."""
import pandas as pd
import streamlit as st

from core.db import rows
from core.extract import CATEGORIES, OBLIGATIONS, TARGETS, candidate_chunks, extract_requirements
from ui.common import get_conn, get_settings, llm_or_none, sidebar

sidebar()
settings, conn = get_settings(), get_conn()

st.title("Факты и ревью")
st.caption("Модель предлагает, аналитик подтверждает. В итоговый отчёт попадают только подтверждённые требования.")

tab_req, tab_q = st.tabs(["Требования к AMR/RMS", "Открытые вопросы"])

STATUS_LABELS = {"auto": "найдено моделью", "confirmed": "подтверждено", "edited": "исправлено", "rejected": "отклонено"}
LABEL_TO_STATUS = {v: k for k, v in STATUS_LABELS.items()}

with tab_req:
    # ---------- запуск извлечения ----------
    pending = candidate_chunks(conn, int(settings.get("extraction.min_relevance", 1)))
    with st.container(border=True):
        c1, c2, c3 = st.columns([3, 1, 1])
        c1.markdown(f"Необработанных фрагментов про AMR/RMS: **{len(pending)}**")
        c1.caption("На ноутбуке без видеокарты один фрагмент обрабатывается от нескольких секунд до минуты. "
                   "Большой архив лучше обрабатывать частями или оставить на ночь.")
        limit = c2.number_input("Обработать за раз", min_value=1, max_value=max(1, len(pending)),
                                value=min(20, max(1, len(pending))))
        llm = llm_or_none()
        if c3.button("Извлечь требования", type="primary", disabled=not pending or llm is None, use_container_width=True):
            ids = {c["doc_id"] for c in pending[: int(limit)]}
            bar = st.progress(0.0)
            counter = {"n": 0}

            def progress(done, total):
                counter["n"] = done
                bar.progress(min(done / min(total, int(limit)), 1.0), text=f"Фрагмент {done} из {min(total, int(limit))}")

            stats = extract_requirements(conn, settings, llm, doc_ids=sorted(ids), progress=progress,
                                         stop=lambda: counter["n"] >= int(limit))
            st.success(f"Найдено кандидатов: {stats['found']}, принято: {stats['accepted']}, "
                       f"отброшено из-за непроверенной цитаты: {stats['rejected_quotes']}, ошибок: {stats['errors']}.")
        if llm is None:
            st.caption("Для извлечения нужна запущенная Ollama с моделью (см. панель слева).")

    # ---------- таблица ревью ----------
    reqs = rows(conn, """SELECT r.id, r.code, r.text, r.target, r.category, r.obligation, r.status, r.comment,
                                r.quote, r.quote_score, r.page, d.rel_path, c.text AS chunk_text
                         FROM requirements r JOIN documents d ON d.id = r.doc_id
                         LEFT JOIN chunks c ON c.id = r.chunk_id ORDER BY r.code""")
    if not reqs:
        st.info("Требований пока нет. Запустите извлечение.")
    else:
        f1, f2, f3 = st.columns(3)
        f_target = f1.multiselect("Адресат", TARGETS)
        f_status = f2.multiselect("Статус", list(STATUS_LABELS.values()), default=["найдено моделью", "подтверждено", "исправлено"])
        f_text = f3.text_input("Поиск по тексту")
        view = [r for r in reqs
                if (not f_target or r["target"] in f_target)
                and (not f_status or STATUS_LABELS[r["status"]] in f_status)
                and (not f_text or f_text.lower() in (r["text"] + " " + (r["quote"] or "")).lower())]
        df = pd.DataFrame(view)
        if df.empty:
            st.caption("Под фильтр ничего не попало.")
        else:
            df["status_label"] = df["status"].map(STATUS_LABELS)
            df["source"] = df["rel_path"] + df["page"].map(lambda p: f", с. {int(p)}" if pd.notna(p) else "")
            edited = st.data_editor(
                df[["id", "code", "text", "target", "category", "obligation", "status_label", "source", "comment"]],
                hide_index=True, use_container_width=True, key="req_editor",
                disabled=["id", "code", "source"],
                column_config={
                    "id": None,
                    "code": st.column_config.TextColumn("ID", width="small"),
                    "text": st.column_config.TextColumn("Требование", width="large"),
                    "target": st.column_config.SelectboxColumn("Кому", options=TARGETS),
                    "category": st.column_config.SelectboxColumn("Категория", options=CATEGORIES),
                    "obligation": st.column_config.SelectboxColumn("Обязательность", options=OBLIGATIONS),
                    "status_label": st.column_config.SelectboxColumn("Статус", options=list(STATUS_LABELS.values())),
                    "source": st.column_config.TextColumn("Источник"),
                    "comment": st.column_config.TextColumn("Комментарий"),
                })
            b1, b2, _ = st.columns([1, 1, 3])
            if b1.button("Сохранить правки", type="primary"):
                for idx, row in edited.iterrows():
                    orig = df.loc[idx]
                    fields = {}
                    for col in ("text", "target", "category", "obligation", "comment"):
                        if str(row[col]) != str(orig[col]):
                            fields[col] = row[col]
                    new_status = LABEL_TO_STATUS.get(row["status_label"], orig["status"])
                    if "text" in fields and new_status == "auto":
                        new_status = "edited"
                    if new_status != orig["status"]:
                        fields["status"] = new_status
                    if fields:
                        sets = ", ".join(f"{k} = ?" for k in fields)
                        conn.execute(f"UPDATE requirements SET {sets}, updated_at = datetime('now') WHERE id = ?",
                                     (*fields.values(), int(row["id"])))
                conn.commit()
                st.success("Сохранено")
                st.rerun()
            if b2.button("Подтвердить все показанные"):
                ids = [int(i) for i in df.loc[df["status"] == "auto", "id"]]
                conn.executemany("UPDATE requirements SET status = 'confirmed', updated_at = datetime('now') WHERE id = ?",
                                 [(i,) for i in ids])
                conn.commit()
                st.rerun()

            # ---------- проверка по источнику ----------
            st.markdown("**Проверка по источнику**")
            code = st.selectbox("Требование", df["code"].tolist(),
                                format_func=lambda c: f"{c} — {df.loc[df['code'] == c, 'text'].iloc[0][:90]}")
            r = df.loc[df["code"] == code].iloc[0]
            with st.container(border=True):
                st.caption(f"{r['source']} · совпадение цитаты {r['quote_score']:.0%}")
                st.markdown(f"> {r['quote']}")
                if isinstance(r["chunk_text"], str):
                    with st.expander("Весь фрагмент документа"):
                        st.text(r["chunk_text"])

with tab_q:
    qs = rows(conn, "SELECT id, text, context, sources, status, answer FROM open_questions ORDER BY id")
    st.caption("Вопросы к заказчику: противоречия документов и отсутствующие решения. Новые строки можно добавлять прямо в таблицу.")
    qdf = pd.DataFrame(qs, columns=["id", "text", "context", "sources", "status", "answer"])
    edited_q = st.data_editor(
        qdf, hide_index=True, use_container_width=True, num_rows="dynamic", key="q_editor",
        column_config={
            "id": None,
            "text": st.column_config.TextColumn("Вопрос", width="large", required=True),
            "context": st.column_config.TextColumn("Контекст"),
            "sources": st.column_config.TextColumn("Источники"),
            "status": st.column_config.SelectboxColumn("Статус", options=["open", "sent", "answered", "closed"], default="open"),
            "answer": st.column_config.TextColumn("Ответ заказчика"),
        })
    if st.button("Сохранить вопросы", type="primary"):
        keep_ids = set()
        for _, row in edited_q.iterrows():
            vals = (row["text"], row["context"], row["sources"], row["status"] or "open", row["answer"])
            if pd.notna(row["id"]):
                keep_ids.add(int(row["id"]))
                conn.execute("UPDATE open_questions SET text=?, context=?, sources=?, status=?, answer=? WHERE id=?",
                             (*vals, int(row["id"])))
            elif isinstance(row["text"], str) and row["text"].strip():
                conn.execute("INSERT INTO open_questions(text, context, sources, status, answer) VALUES (?,?,?,?,?)", vals)
        for q in qs:
            if q["id"] not in keep_ids:
                conn.execute("DELETE FROM open_questions WHERE id = ?", (q["id"],))
        conn.commit()
        st.success("Сохранено")
        st.rerun()
