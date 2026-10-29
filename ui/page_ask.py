"""Экран 2. Вопросы к архиву: ответ модели строго по найденным фрагментам, со ссылками на документ и страницу."""
import streamlit as st

from core.ask import ask, source_label
from ui.common import get_conn, get_settings, llm_or_none, sidebar

sidebar()
settings, conn = get_settings(), get_conn()

st.title("Вопросы к архиву")
st.caption("Ответ строится только по найденным фрагментам. Номера в скобках [1], [2] — ссылки на источники справа.")

with st.form("ask"):
    c1, c2, c3 = st.columns([5, 2, 1])
    question = c1.text_input("Вопрос", placeholder="Какая система создаёт транспортное задание для AMR?",
                             label_visibility="collapsed")
    scope = c2.selectbox("Область", ["Только актуальные редакции", "Все документы"], label_visibility="collapsed")
    submitted = c3.form_submit_button("Спросить", type="primary", use_container_width=True)

if submitted and question.strip():
    llm = llm_or_none()
    with st.spinner("Ищу фрагменты и формулирую ответ…" if llm else "Ищу фрагменты…"):
        st.session_state["last_answer"] = (question, ask(conn, settings, question, llm=llm,
                                                         only_current=scope.startswith("Только")))

if "last_answer" in st.session_state:
    question, ans = st.session_state["last_answer"]
    left, right = st.columns([3, 2], gap="large")
    with left:
        st.markdown(f"**Вопрос:** {question}")
        if ans.error:
            st.error(f"Модель не ответила: {ans.error}")
        if ans.text:
            with st.container(border=True):
                st.markdown(ans.text)
        elif not ans.error and ans.hits:
            st.info("Модель недоступна — ниже найденные фрагменты без сводного ответа.")
        if ans.hits:
            st.markdown("**Нашли противоречие или пробел?**")
            with st.form("to_question", clear_on_submit=True):
                q_text = st.text_area("Формулировка вопроса заказчику", value=question, height=80)
                if st.form_submit_button("Добавить в открытые вопросы"):
                    sources = "; ".join(source_label(h) for h in ans.hits[:3])
                    conn.execute("INSERT INTO open_questions(text, context, sources) VALUES (?, ?, ?)",
                                 (q_text, (ans.text or "")[:1000], sources))
                    conn.commit()
                    st.success("Вопрос добавлен (раздел «Факты и ревью» → «Открытые вопросы»).")
    with right:
        st.markdown("**Найденные фрагменты**")
        for n, h in enumerate(ans.hits, start=1):
            badges = []
            if not h.is_current:
                badges.append(":orange[устаревшая редакция]")
            if h.approval and h.approval != "не указано":
                badges.append(f":green[{h.approval}]")
            if h.side and h.side != "не указано":
                badges.append(h.side)
            with st.expander(f"[{n}] {source_label(h)}", expanded=n <= 2):
                if badges:
                    st.markdown(" · ".join(badges))
                if h.section:
                    st.caption(h.section)
                st.text(h.text)
