"""Версия 2: процессы, обмены, ответственность, противоречия, схемы — через ядро и через HTTP API.
Без Ollama: поддельная модель (tests/fake_llm.py) разбирает вымышленный демо-архив."""
from __future__ import annotations

import io
import json
import sys
import tempfile
import time
import unittest
import xml.dom.minidom
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "scripts", Path(__file__).resolve().parent):
    sys.path.insert(0, str(p))

from core import consolidate, db, diagrams  # noqa: E402
from core.config import load_settings  # noqa: E402
from core.facts import pending_counts, run_extraction  # noqa: E402
from core.pipeline import run_all  # noqa: E402
from core.vocab import load_vocab  # noqa: E402
from fake_llm import FakeLLM  # noqa: E402
from make_demo_archive import make  # noqa: E402


class CoreV2Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls.tmp.name)
        cls.settings = load_settings({"paths": {"data_dir": str(tmp / "d"), "output_dir": str(tmp / "out"),
                                                "db_file": str(tmp / "d" / "v2.sqlite")}})
        cls.conn = db.connect(cls.settings.db_file)
        run_all(cls.conn, cls.settings, make(tmp / "archive"), llm=FakeLLM())
        cls.stats = run_extraction(cls.conn, cls.settings, FakeLLM())

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        cls.tmp.cleanup()

    def test_vocab(self):
        v = load_vocab()
        self.assertIn("RMS", v.systems)
        self.assertEqual(v.system("sap ewm"), "SAP EWM")
        self.assertEqual(v.system("Система управления роботами"), "RMS")
        self.assertEqual(v.system("что-то своё"), "Другое")
        self.assertEqual(v.function_key("Подтверждение выполнения перевозки"), "confirm_delivery")
        self.assertEqual(v.amr_role("неизвестно"), v.amr_roles[-1])

    def test_extraction_all_kinds_and_quote_check(self):
        bk = self.stats["by_kind"]
        self.assertEqual(set(bk), {"requirements", "processes", "integration"})
        for k in bk:
            self.assertGreater(bk[k]["accepted"], 0, k)
            self.assertGreater(bk[k]["rejected_quotes"], 0, k)        # «выдуманные» факты отброшены
        self.assertEqual(pending_counts(self.conn), {"requirements": 0, "processes": 0, "integration": 0})
        names = [r["name"] for r in db.rows(self.conn, "SELECT name FROM processes")]
        self.assertIn("Подвоз комплектующих к участку сборки", names)
        self.assertNotIn("Телепортация паллет", names)
        robot = db.one(self.conn, "SELECT * FROM processes WHERE amr_role = 'роботизирован'")
        steps = json.loads(robot["steps"])
        self.assertEqual([s["actor"] for s in steps], ["Оператор", "SAP EWM", "RMS", "AMR"])
        pairs = {(r["source_system"], r["target_system"]) for r in db.rows(self.conn, "SELECT * FROM interfaces")}
        self.assertIn(("SAP EWM", "RMS"), pairs)
        self.assertNotIn(("ERP", "AMR"), pairs)
        # повторный запуск ничего не обрабатывает
        self.assertEqual(run_extraction(self.conn, self.settings, FakeLLM())["chunks"], 0)

    def test_matrix_and_conflicts(self):
        m = consolidate.responsibility_matrix(self.conn)
        state = {r["key"]: r for r in m["rows"]}
        self.assertEqual(state["initiate_transport"]["systems"], ["SAP EWM"])
        self.assertEqual(state["confirm_delivery"]["state"], "conflict")
        self.assertEqual(state["charging"]["state"], "gap")
        items = {c["key"]: c for c in consolidate.refresh(self.conn)}
        self.assertEqual(items["resp:confirm_delivery"]["kind"], "contradiction")
        self.assertIn("gap:resp:charging", items)
        self.assertIn("gap:proto:RMS:AMR", items)                     # обмен RMS→AMR есть, протокол не указан
        self.assertNotIn("gap:link:RMS:ERP+MES+SAP EWM+WMS", items)  # обмен RMS с учётной системой есть
        text, context, sources = consolidate.question_text(items["resp:confirm_delivery"])
        self.assertIn("MES или SAP EWM", text)
        self.assertIn("ТЗ_на_роботизацию", sources)

    def test_revision_detected_when_doc_outdated(self):
        tz = db.one(self.conn, "SELECT id FROM documents WHERE rel_path LIKE '%ТЗ_на%'")
        self.conn.execute("UPDATE documents SET is_current = 0 WHERE id = ?", (tz["id"],))
        try:
            keys = {d.key: d.kind for d in consolidate.detect(self.conn)}
            self.assertEqual(keys.get("rev:confirm_delivery"), "revision")
            self.assertNotIn("resp:confirm_delivery", keys)
        finally:
            self.conn.execute("UPDATE documents SET is_current = 1 WHERE id = ?", (tz["id"],))
            self.conn.commit()

    def test_diagrams(self):
        ds = diagrams.all_diagrams(self.conn)
        keys = [d.key for d in ds]
        self.assertEqual(keys[:2], ["systems", "logistics"])
        self.assertTrue(any(k.startswith("process-PR-") for k in keys))
        for d in ds:
            self.assertFalse(d.empty, d.key)
            xml.dom.minidom.parseString(diagrams.to_svg(d))          # корректный XML
            self.assertIn("flowchart", diagrams.to_mermaid(d))
        drawio = diagrams.to_drawio(ds)
        dom = xml.dom.minidom.parseString(drawio)
        self.assertEqual(len(dom.getElementsByTagName("diagram")), len(ds))
        sysd = ds[0]
        self.assertEqual({n.id for n in sysd.nodes} >= {"SAP EWM", "RMS", "AMR"}, True)
        logd = ds[1]
        self.assertTrue(any(e.accent for e in logd.edges))            # поток с роботом выделен
        self.assertTrue(any(not e.accent for e in logd.edges))        # ручной — нет

    def test_eval_script(self):
        from eval_requirements import evaluate, load_gold, similarity
        self.assertGreater(similarity("RMS должна поддерживать приоритеты заданий", "Система поддерживает приоритеты заданий RMS"), 0.6)
        gold = load_gold(ROOT / "eval" / "requirements_gold.example.csv")
        res = evaluate(self.conn, gold)
        self.assertEqual(res["gold"], len(gold))
        self.assertGreater(res["matched"], 0)
        self.assertTrue(0 < res["recall"] <= 1 and 0 < res["precision"] <= 1)

    def test_search_limits_hits_per_document(self):
        from core.index import search
        hits = search(self.conn, "робот RMS задание", llm=FakeLLM(), top_k=6, max_per_doc=1)
        docs = [h.doc_id for h in hits]
        n_docs = len({h.doc_id for h in search(self.conn, "робот RMS задание", llm=FakeLLM(), top_k=50, max_per_doc=0)})
        self.assertEqual(len(set(docs[:n_docs])), min(n_docs, len(docs)))   # сначала по одному из каждого документа

    def test_guess_route(self):
        from core.routes import guess_route
        self.assertEqual(guess_route("Подвоз материалов со склада к участкам сборки"), ("Склад", "Участки сборки"))
        self.assertEqual(guess_route("Подвоз комплектующих со склада к участку сборки"), ("Склад", "Участок сборки"))
        self.assertEqual(guess_route("Вывоз пустой тары с участка сборки в зону возвратной тары выполняется вручную"),
                         ("Участок сборки", "Зона возвратной тары"))
        self.assertEqual(guess_route("", "робот забирает тележку в зоне комплектации склада и доставляет её в зону подачи PSA"),
                         ("Зона комплектации склада", "Зона подачи PSA"))
        self.assertEqual(guess_route("Доставка тары от линии упаковки до склада готовой продукции"),
                         ("Линия упаковки", "Склад готовой продукции"))
        self.assertEqual(guess_route("Возврат статуса выполнения задания", "RMS возвращает статус в EWM"), (None, None))

    def test_missing_routes_are_filled(self):
        # так вела себя qwen3:8b на реальном запуске: процесс есть, «откуда» и «куда» пустые
        from core.facts import fill_missing_routes, save_processes
        chunk = db.one(self.conn, "SELECT c.id, c.doc_id, c.text, c.page_from FROM chunks c JOIN documents d ON d.id = c.doc_id "
                                  "WHERE d.is_current = 1 AND c.text LIKE '%Вывоз пустой тары%'")
        quote = "Вывоз пустой тары с участка сборки в зону возвратной тары выполняется вручную водителем погрузчика."
        st = save_processes(self.conn, chunk, {"processes": [{"name": "Подвоз материалов со склада к участкам сборки",
                                                              "initiator": "ERP", "amr_role": "роботизирован", "steps": [],
                                                              "from_location": "", "to_location": "", "quote": quote}]},
                            0.9, load_vocab())
        self.assertEqual(st["accepted"], 1)
        p = db.one(self.conn, "SELECT * FROM processes WHERE name = 'Подвоз материалов со склада к участкам сборки'")
        self.assertEqual((p["from_location"], p["to_location"]), ("Склад", "Участки сборки"))
        # уже сохранённые без мест — дозаполняются при старте сервера
        self.conn.execute("UPDATE processes SET from_location = NULL, to_location = '' WHERE id = ?", (p["id"],))
        self.assertGreaterEqual(fill_missing_routes(self.conn), 1)
        p = db.one(self.conn, "SELECT * FROM processes WHERE id = ?", (p["id"],))
        self.assertEqual(p["to_location"], "Участки сборки")
        # заполненное аналитиком не трогаем
        self.conn.execute("UPDATE processes SET from_location = 'Мой склад', to_location = '' WHERE id = ?", (p["id"],))
        fill_missing_routes(self.conn)
        self.assertEqual(db.one(self.conn, "SELECT from_location FROM processes WHERE id = ?", (p["id"],))["from_location"], "Мой склад")
        self.conn.execute("DELETE FROM processes WHERE id = ?", (p["id"],))
        self.conn.commit()

    def test_wrap(self):
        self.assertEqual(diagrams.wrap("один два три", 8), ["один два", "три"])
        self.assertTrue(diagrams.wrap("слово " * 30, 10, 2)[-1].endswith("…"))


class ApiV2Test(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        from api.app import create_app
        self.tmp = tempfile.TemporaryDirectory()
        tmp = Path(self.tmp.name)
        settings = load_settings({"paths": {"data_dir": str(tmp / "data"), "output_dir": str(tmp / "out"),
                                            "db_file": str(tmp / "data" / "api.sqlite")}})
        self.archive = make(tmp / "archive")
        self.client = TestClient(create_app(settings, FakeLLM()))

    def tearDown(self):
        self.tmp.cleanup()

    def wait(self, job: dict) -> dict:
        end = time.time() + 60
        while time.time() < end:
            j = self.client.get(f"/api/jobs/{job['id']}").json()
            if j["state"] != "running":
                return j
            time.sleep(0.05)
        self.fail("задача не завершилась")

    def prepare(self, kinds=None):
        r = self.client.post("/api/archive/process", json={"path": str(self.archive)})
        self.assertEqual(self.wait(r.json())["state"], "done")
        body = {"limit": 100} | ({"kinds": kinds} if kinds else {})
        r = self.client.post("/api/extraction/run", json=body)
        self.assertEqual(r.status_code, 202, r.text)
        job = self.wait(r.json())
        self.assertEqual(job["state"], "done", job)
        return job["result"]

    def test_meta_has_vocab(self):
        m = self.client.get("/api/meta").json()
        self.assertIn("RMS", m["systems"])
        self.assertIn("confirm_delivery", [f["key"] for f in m["functions"]])
        self.assertEqual([k["key"] for k in m["fact_kinds"]], ["requirements", "processes", "integration"])

    def test_selected_kinds_only(self):
        res = self.prepare(["processes"])
        self.assertEqual(res["kinds"], ["processes"])
        pend = self.client.get("/api/extraction/pending").json()
        self.assertEqual(pend["by_kind"]["processes"], 0)
        self.assertGreater(pend["by_kind"]["requirements"], 0)
        self.assertEqual(self.client.get("/api/requirements").json()["total"], 0)
        self.assertGreater(self.client.get("/api/processes").json()["total"], 0)
        self.assertEqual(self.client.post("/api/extraction/run", json={"kinds": ["bogus"]}).status_code, 422)

    def test_fact_review_routes(self):
        self.prepare()
        procs = self.client.get("/api/processes").json()
        p = next(x for x in procs["items"] if x["amr_role"] == "роботизирован")
        self.assertIsInstance(p["steps"], list)
        self.assertNotIn("chunk_text", p)
        detail = self.client.get(f"/api/processes/{p['id']}").json()
        self.assertTrue(detail["chunk_text"] and detail["quote"])
        # правка шагов: «найдено моделью» → «исправлено»
        steps = detail["steps"] + [{"actor": "AMR", "action": "возвращается в зону ожидания"}]
        r = self.client.patch(f"/api/processes/{p['id']}", json={"steps": steps})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((r.json()["status"], len(r.json()["steps"])), ("edited", len(steps)))
        self.assertEqual(self.client.patch(f"/api/processes/{p['id']}", json={"initiator": "Марсоход"}).status_code, 422)
        self.assertEqual(self.client.patch(f"/api/processes/{p['id']}", json={"name": None}).status_code, 422)
        self.assertEqual(self.client.get("/api/processes/99999").status_code, 404)
        # только комментарий — статус не меняется
        i = self.client.get("/api/interfaces").json()["items"][0]
        self.assertEqual(self.client.patch(f"/api/interfaces/{i['id']}", json={"comment": "проверить"}).json()["status"], "auto")
        self.assertEqual(self.client.patch(f"/api/interfaces/{i['id']}", json={"protocol": "REST"}).json()["status"], "edited")
        # массовое подтверждение
        ids = [x["id"] for x in self.client.get("/api/responsibilities").json()["items"]]
        self.assertEqual(self.client.post("/api/responsibilities/confirm", json={"ids": ids}).json()["confirmed"], len(ids))
        r = self.client.get("/api/responsibilities", params={"q": "MES"}).json()
        self.assertTrue(r["items"] and all(x["function_title"] for x in r["items"]))

    def test_diagrams_and_matrix(self):
        self.assertEqual(self.client.get("/api/diagrams/systems.svg").status_code, 404)    # пусто — 404 с подсказкой
        self.prepare()
        for url in ("/api/diagrams/systems.svg", "/api/diagrams/logistics.svg"):
            r = self.client.get(url)
            self.assertEqual(r.status_code, 200, url)
            self.assertTrue(r.headers["content-type"].startswith("image/svg+xml"))
            xml.dom.minidom.parseString(r.text)
        # только подтверждённые: пока ничего не подтверждено — схемы пусты
        self.assertEqual(self.client.get("/api/diagrams/systems.svg", params={"include_auto": False}).status_code, 404)
        pid = self.client.get("/api/processes").json()["items"][0]["id"]
        self.assertEqual(self.client.get(f"/api/diagrams/process/{pid}.svg").status_code, 200)
        self.assertIn("systems", self.client.get("/api/diagrams/mermaid").json())
        m = self.client.get("/api/model/matrix").json()
        self.assertIn("RMS", m["systems"])
        ov = self.client.get("/api/model/overview").json()
        self.assertGreater(ov["processes"]["total"], 0)

    def test_conflicts_workflow(self):
        self.prepare()
        data = self.client.get("/api/conflicts").json()
        self.assertGreater(data["open"], 0)
        c = next(x for x in data["items"] if x["key"] == "resp:confirm_delivery")
        self.assertEqual({v["value"] for v in c["details"]["variants"]}, {"MES", "SAP EWM"})
        # в вопросы заказчику
        q = self.client.post(f"/api/conflicts/{c['id']}/question").json()
        self.assertEqual(q["status"], "question")
        questions = self.client.get("/api/open-questions").json()
        self.assertEqual(len(questions), 1)
        self.assertIn("MES или SAP EWM", questions[0]["text"])
        # повторно — вопрос не дублируется
        self.client.post(f"/api/conflicts/{c['id']}/question")
        self.assertEqual(len(self.client.get("/api/open-questions").json()), 1)
        # принять вариант
        self.assertEqual(self.client.post(f"/api/conflicts/{c['id']}/resolve", json={"value": "ERP"}).status_code, 422)
        r = self.client.post(f"/api/conflicts/{c['id']}/resolve", json={"value": "SAP EWM"}).json()
        self.assertEqual((r["status"], r["resolution"]), ("resolved", "SAP EWM"))
        rs = {x["system"]: x["status"] for x in self.client.get("/api/responsibilities", params={"q": "confirm_delivery"}).json()["items"]}
        self.assertEqual(rs, {"SAP EWM": "confirmed", "MES": "rejected"})
        # после пересчёта решение сохраняется
        again = next(x for x in self.client.get("/api/conflicts").json()["items"] if x["id"] == c["id"])
        self.assertEqual(again["status"], "resolved")
        # пробел — «не противоречие» и обратно
        gap = next(x for x in self.client.get("/api/conflicts").json()["items"] if x["kind"] == "gap")
        self.assertEqual(self.client.post(f"/api/conflicts/{gap['id']}/dismiss").json()["status"], "dismissed")
        self.assertEqual(self.client.post(f"/api/conflicts/{gap['id']}/reopen").json()["status"], "open")
        self.assertEqual(self.client.post("/api/conflicts/9999/dismiss").status_code, 404)

    def test_report_v2(self):
        self.prepare()
        s = self.client.get("/api/report/summary").json()
        self.assertEqual([x["state"] for x in s["sections"][1:4]], ["черновик"] * 3)
        self.assertGreater(s["counts"]["unreviewed_facts"], 0)
        z = self.client.post("/api/report/diagrams", json={"include_auto": True}).json()
        r = self.client.get(z["url"])
        self.assertEqual(r.status_code, 200)
        names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
        self.assertIn("Схемы_AMR_RMS.drawio", names)
        self.assertTrue(any(n.startswith("svg/") for n in names))
        docx = self.client.post("/api/report/docx", json={"include_auto": True}).json()
        from docx import Document
        doc = Document(io.BytesIO(self.client.get(docx["url"]).content))
        heads = [p.text for p in doc.paragraphs if p.style.name.startswith("Heading")]
        self.assertIn("5.1. Функции и границы ответственности", heads)
        self.assertIn("6.1. Противоречия между документами", heads)
        self.assertTrue(any(h.startswith("PR-") for h in heads))
        cells = " ".join(c.text for t in doc.tables for row in t.rows for c in row.cells)
        self.assertIn("транспортное задание", cells)
        if diagrams.png_available():
            self.assertGreaterEqual(len(doc.inline_shapes), 2)


if __name__ == "__main__":
    unittest.main()
