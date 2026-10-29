"""Дымовой тест экранов: выполняет страницы Streamlit с заглушкой вместо streamlit на демо-базе.
Ловит ошибки в работе с данными (pandas, SQL) без запуска браузера и без установленного streamlit."""
from __future__ import annotations

import runpy
import sys
import tempfile
import types
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))


class _Stop(Exception):
    pass


class Dummy:
    def __call__(self, *a, **k):
        return Dummy()

    def __getattr__(self, name):
        return Dummy()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def make_stub(answers: dict) -> types.ModuleType:
    st = types.ModuleType("streamlit")
    st.session_state = {}
    st.column_config = Dummy()
    st.sidebar = Dummy()

    def cache(func=None, **_):
        def wrap(f):
            f.clear = lambda: None
            return f
        return wrap(func) if func else wrap

    class Proxy(Dummy):
        """Колонка/вкладка: виджеты внутри ведут себя так же, как st.*"""
        def __getattr__(self, name):
            return getattr(st, name)

    def columns(spec, **_):
        n = spec if isinstance(spec, int) else len(spec)
        return [Proxy() for _ in range(n)]

    def stop():
        raise _Stop()

    def data_editor(df, key=None, **_):
        edit = answers.get(("edit", key))
        return edit(df.copy()) if edit else df

    st.cache_resource = cache
    st.cache_data = cache
    st.columns = columns
    st.tabs = lambda labels: [Proxy() for _ in labels]
    st.stop = stop
    st.data_editor = data_editor
    st.button = lambda label, **k: answers.get(label, False)
    st.form_submit_button = lambda label, **k: answers.get(label, False)
    st.text_input = lambda label, value="", **k: answers.get(label, value)
    st.text_area = lambda label, value="", **k: answers.get(label, value)
    st.toggle = lambda label, value=False, **k: value
    st.checkbox = lambda label, value=False, **k: value
    st.selectbox = lambda label, options, **k: answers.get(label, list(options)[0] if len(options) else None)
    st.multiselect = lambda label, options, default=None, **k: default or []
    st.number_input = lambda label, value=0, **k: value
    st.file_uploader = lambda *a, **k: None
    st.progress = lambda *a, **k: Dummy()
    st.dataframe = lambda *a, **k: None

    def _any(name):
        return Dummy()

    st.__getattr__ = _any  # type: ignore[attr-defined]
    return st


class UISmokeTest(unittest.TestCase):
    def setUp(self):
        from core import db
        from core.config import load_settings
        from core.extract import extract_requirements
        from core.pipeline import run_all
        from fake_llm import FakeLLM
        from make_demo_archive import make

        self.tmp = tempfile.TemporaryDirectory()
        tmp = Path(self.tmp.name)
        self.settings = load_settings({"paths": {"data_dir": str(tmp / "data"), "output_dir": str(tmp / "out"),
                                                 "db_file": str(tmp / "data" / "ui.sqlite")}})
        self.conn = db.connect(self.settings.db_file)
        self.archive = make(tmp / "archive")
        run_all(self.conn, self.settings, self.archive, llm=FakeLLM())
        extract_requirements(self.conn, self.settings, FakeLLM())
        self.fake = FakeLLM()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()
        for m in list(sys.modules):
            if m == "streamlit" or m.startswith("ui."):
                sys.modules.pop(m)

    def run_page(self, page: str, answers: dict | None = None, session: dict | None = None):
        st = make_stub(answers or {})
        st.session_state.update(session or {})
        sys.modules["streamlit"] = st
        sys.modules.pop("ui.common", None)
        import ui.common as common
        common.get_settings = lambda: self.settings
        common.get_conn = lambda: self.conn
        common.get_llm = lambda: self.fake
        common.llm_status = lambda: {"ok": True, "models": [], "missing": [], "error": None}
        common.llm_or_none = lambda: self.fake
        try:
            runpy.run_path(str(ROOT / "ui" / page), run_name="__page__")
        except _Stop:
            pass
        return st

    def test_archive_page(self):
        self.run_page("page_archive.py")
        self.run_page("page_archive.py", {"Путь к папке или архиву": str(self.archive), "Запустить обработку": True})

        def edit(df):
            df.loc[0, "doc_type"] = "Протокол"
            df.loc[0, "is_current"] = False
            return df

        self.run_page("page_archive.py", {("edit", "docs_editor"): edit, "Сохранить правки карточек": True},
                      session={"docs_editor": {"edited_rows": {0: {"doc_type": "Протокол"}}}})
        from core.db import rows
        manual = rows(self.conn, "SELECT doc_type, is_current FROM documents WHERE card_source = 'manual'")
        self.assertEqual(manual, [{"doc_type": "Протокол", "is_current": 0}])

    def test_ask_page(self):
        st = self.run_page("page_ask.py", {"Вопрос": "Кто создаёт транспортное задание?", "Спросить": True})
        self.assertIn("last_answer", st.session_state)
        self.run_page("page_ask.py", {"Добавить в открытые вопросы": True}, session=dict(st.session_state))
        from core.db import rows
        self.assertEqual(len(rows(self.conn, "SELECT * FROM open_questions")), 1)

    def test_facts_page(self):
        self.run_page("page_facts.py")
        self.run_page("page_facts.py", {"Подтвердить все показанные": True})
        from core.db import rows
        self.assertTrue(all(r["status"] == "confirmed" for r in rows(self.conn, "SELECT status FROM requirements")))
        self.run_page("page_facts.py", {"Сохранить правки": True, "Сохранить вопросы": True})

    def test_report_page(self):
        st = self.run_page("page_report.py", {"Сформировать Word": True, "Таблицы в Excel": True})
        self.assertTrue(st.session_state["docx"].exists())
        self.assertTrue(st.session_state["xlsx"].exists())


if __name__ == "__main__":
    unittest.main()
