"""DocAnalyzer AMR/RMS — локальный сервис анализа архива проектной документации.

Запуск:  streamlit run app.py   (или run.bat в Windows)
"""
import streamlit as st

st.set_page_config(page_title="DocAnalyzer AMR/RMS", layout="wide")

pages = [
    st.Page("ui/page_archive.py", title="Архив и обработка", default=True),
    st.Page("ui/page_ask.py", title="Вопросы к архиву"),
    st.Page("ui/page_facts.py", title="Факты и ревью"),
    st.Page("ui/page_report.py", title="Отчёт"),
]
st.navigation(pages).run()
