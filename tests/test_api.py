"""Тесты HTTP-слоя: те же сценарии, что в дымовом тесте экранов Streamlit, но через /api/*. Без Ollama (FakeLLM)."""
from __future__ import annotations

import io
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "scripts", Path(__file__).resolve().parent):
    sys.path.insert(0, str(p))

from api.app import create_app  # noqa: E402
from core.config import load_settings  # noqa: E402
from fake_llm import FakeLLM  # noqa: E402
from make_demo_archive import make  # noqa: E402


class ApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        tmp = Path(self.tmp.name)
        settings = load_settings({"paths": {"data_dir": str(tmp / "data"), "output_dir": str(tmp / "out"),
                                            "db_file": str(tmp / "data" / "api.sqlite")}})
        self.archive = make(tmp / "archive")
        self.client = TestClient(create_app(settings, FakeLLM()))

    def tearDown(self):
        self.tmp.cleanup()

    # --- помощники ---
    def wait(self, job: dict, timeout: float = 60) -> dict:
        end = time.time() + timeout
        while time.time() < end:
            j = self.client.get(f"/api/jobs/{job['id']}").json()
            if j["state"] != "running":
                return j
            time.sleep(0.05)
        self.fail("задача не завершилась")

    def process(self) -> dict:
        r = self.client.post("/api/archive/process", json={"path": str(self.archive)})
        self.assertEqual(r.status_code, 202, r.text)
        job = self.wait(r.json())
        self.assertEqual(job["state"], "done", job)
        return job

    def extract(self) -> dict:
        r = self.client.post("/api/extraction/run", json={"limit": 50})
        self.assertEqual(r.status_code, 202, r.text)
        job = self.wait(r.json())
        self.assertEqual(job["state"], "done", job)
        return job

    # --- система ---
    def test_status_and_meta(self):
        s = self.client.get("/api/status").json()
        self.assertEqual(s["state"], "ok")
        self.assertEqual(s["chat_model"], "fake-chat")
        m = self.client.get("/api/meta").json()
        self.assertIn("Протокол", m["doc_types"])
        self.assertEqual(m["requirement_statuses"]["auto"], "найдено моделью")

    # --- архив ---
    def test_empty_archive(self):
        self.assertEqual(self.client.get("/api/archive/summary").json()["files"], 0)
        self.assertEqual(self.client.get("/api/documents").json(), {"total": 0, "items": []})

    def test_missing_path_is_404(self):
        r = self.client.post("/api/archive/process", json={"path": str(self.archive / "нет-такого")})
        self.assertEqual(r.status_code, 404)
        self.assertIn("Путь не найден", r.json()["detail"])

    def test_process_and_catalog(self):
        job = self.process()
        stats = job["result"]
        self.assertGreater(stats["found"], 0)
        self.assertTrue({s["name"] for s in job["stages"]} >= {"Извлечение текста", "Карточки документов"})
        summ = self.client.get("/api/archive/summary").json()
        self.assertEqual(summ["files"], stats["added"])
        docs = self.client.get("/api/documents").json()
        self.assertEqual(docs["total"], len(docs["items"]))
        self.assertIsInstance(docs["items"][0]["is_current"], bool)
        # фильтры
        some = docs["items"][0]["rel_path"][:6]
        found = self.client.get("/api/documents", params={"query": some}).json()["items"]
        self.assertTrue(found and all(some.lower() in d["rel_path"].lower() or some.lower() in (d["title"] or "").lower() for d in found))
        old = self.client.get("/api/documents", params={"hide_outdated": True}).json()["items"]
        self.assertTrue(all(d["is_current"] for d in old))

    def test_repeat_run_adds_only_duplicates(self):
        first = self.process()["result"]
        second = self.process()["result"]
        self.assertEqual(second["added"], 0)
        self.assertEqual(second["duplicates"], first["added"] + first["duplicates"])

    def test_upload_zip(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("протокол.txt", "Робот должен возвращаться на зарядку при уровне заряда ниже 20%.")
        r = self.client.post("/api/archive/upload", files={"file": ("архив.zip", buf.getvalue(), "application/zip")})
        self.assertEqual(r.status_code, 202, r.text)
        self.assertEqual(self.wait(r.json())["state"], "done")
        self.assertEqual(self.client.post("/api/archive/upload", files={"file": ("a.txt", b"x")}).status_code, 400)

    def test_patch_document_marks_manual(self):
        self.process()
        doc = self.client.get("/api/documents").json()["items"][0]
        r = self.client.patch(f"/api/documents/{doc['id']}", json={"doc_type": "Протокол", "is_current": False})
        self.assertEqual(r.status_code, 200, r.text)
        out = r.json()
        self.assertEqual((out["doc_type"], out["is_current"], out["card_source"]), ("Протокол", False, "manual"))
        # вне справочника, неверная релевантность, пустое обязательное поле
        self.assertEqual(self.client.patch(f"/api/documents/{doc['id']}", json={"doc_type": "Выдумка"}).status_code, 422)
        self.assertEqual(self.client.patch(f"/api/documents/{doc['id']}", json={"relevance": 9}).status_code, 422)
        self.assertEqual(self.client.patch(f"/api/documents/{doc['id']}", json={"side": None}).status_code, 422)
        self.assertEqual(self.client.patch("/api/documents/99999", json={"revision": "1"}).status_code, 404)

    def test_unchanged_patch_keeps_auto_card(self):
        self.process()
        doc = self.client.get("/api/documents").json()["items"][0]
        out = self.client.patch(f"/api/documents/{doc['id']}", json={"doc_type": doc["doc_type"]}).json()
        self.assertEqual(out["card_source"], doc["card_source"])

    def test_embeddings_job(self):
        self.process()
        r = self.client.post("/api/archive/embeddings")
        self.assertEqual(r.status_code, 202)
        self.assertEqual(self.wait(r.json())["state"], "done")
        self.assertFalse(self.client.get("/api/archive/summary").json()["embeddings_pending"])

    # --- вопросы ---
    def test_ask_and_save_open_question(self):
        self.process()
        r = self.client.post("/api/ask", json={"question": "Кто создаёт транспортное задание?"})
        self.assertEqual(r.status_code, 200, r.text)
        ans = r.json()
        self.assertTrue(ans["model_available"] and ans["text"] and ans["hits"])
        self.assertEqual(ans["hits"][0]["n"], 1)
        self.assertTrue(ans["sources"])
        q = self.client.post("/api/open-questions", json={"text": ans["question"], "context": ans["text"],
                                                           "sources": ans["sources"]})
        self.assertEqual(q.status_code, 201)
        self.assertEqual(len(self.client.get("/api/open-questions").json()), 1)

    def test_ask_blank_is_422(self):
        self.assertEqual(self.client.post("/api/ask", json={"question": ""}).status_code, 422)

    # --- факты ---
    def test_extraction_and_review(self):
        self.process()
        self.assertGreater(self.client.get("/api/extraction/pending").json()["pending"], 0)
        stats = self.extract()["result"]
        self.assertGreater(stats["accepted"], 0)
        self.assertGreater(stats["rejected_quotes"], 0)     # «выдуманное» требование отброшено проверкой цитаты
        self.assertEqual(self.client.get("/api/extraction/pending").json()["pending"], 0)
        self.assertEqual(self.client.post("/api/extraction/run", json={"limit": 5}).status_code, 409)

        data = self.client.get("/api/requirements").json()
        self.assertEqual(data["total"], stats["accepted"])
        self.assertEqual(data["by_status"], {"auto": stats["accepted"]})
        item = data["items"][0]
        self.assertNotIn("chunk_text", item)
        self.assertEqual(self.client.get("/api/requirements", params={"status": "confirmed"}).json()["items"], [])
        rms = self.client.get("/api/requirements", params={"target": "RMS"}).json()["items"]
        self.assertEqual(len(rms), sum(r["target"] == "RMS" for r in data["items"]))

        detail = self.client.get(f"/api/requirements/{item['id']}").json()
        self.assertTrue(detail["quote"])
        self.assertGreaterEqual(detail["quote_score"], 0.9)
        self.assertTrue(detail["chunk_text"])

        # правка текста у «найдено моделью» → «исправлено»
        edited = self.client.patch(f"/api/requirements/{item['id']}", json={"text": "Новая формулировка"}).json()
        self.assertEqual((edited["status"], edited["status_label"]), ("edited", "исправлено"))
        # явный статус при правке текста сохраняется
        other = data["items"][1]
        r = self.client.patch(f"/api/requirements/{other['id']}", json={"text": "Ещё формулировка", "status": "confirmed"})
        self.assertEqual(r.json()["status"], "confirmed")
        self.assertEqual(self.client.patch(f"/api/requirements/{item['id']}", json={"text": None}).status_code, 422)

        # массовое подтверждение трогает только «найдено моделью»
        ids = [r["id"] for r in self.client.get("/api/requirements").json()["items"]]
        n = self.client.post("/api/requirements/confirm", json={"ids": ids}).json()["confirmed"]
        self.assertEqual(n, len(ids) - 2)
        by = self.client.get("/api/requirements").json()["by_status"]
        self.assertEqual(by.get("auto", 0), 0)
        self.assertEqual(by["edited"], 1)

    def test_open_questions_crud(self):
        q = self.client.post("/api/open-questions", json={"text": "Кто владелец интеграции?"}).json()
        self.assertEqual(q["status"], "open")
        upd = self.client.patch(f"/api/open-questions/{q['id']}", json={"status": "answered", "answer": "Заказчик"}).json()
        self.assertEqual((upd["status"], upd["answer"]), ("answered", "Заказчик"))
        self.assertEqual(self.client.patch(f"/api/open-questions/{q['id']}", json={"status": "bogus"}).status_code, 422)
        self.assertEqual(self.client.delete(f"/api/open-questions/{q['id']}").status_code, 204)
        self.assertEqual(self.client.delete(f"/api/open-questions/{q['id']}").status_code, 404)

    # --- отчёт ---
    def test_report(self):
        self.assertFalse(self.client.get("/api/report/summary").json()["can_export"])
        self.assertEqual(self.client.post("/api/report/docx", json={}).status_code, 409)
        self.process()
        self.extract()
        s = self.client.get("/api/report/summary").json()
        self.assertTrue(s["can_export"])
        self.assertEqual(s["sections"][0]["state"], "готов")
        self.assertEqual(s["sections"][4]["state"], "черновик")
        self.assertEqual(s["sections"][1]["tone"], "gray")

        docx = self.client.post("/api/report/docx", json={"include_auto": True, "project": "Тест"}).json()
        xlsx = self.client.post("/api/report/xlsx").json()
        for f, magic in ((docx, b"PK"), (xlsx, b"PK")):
            r = self.client.get(f["url"])
            self.assertEqual(r.status_code, 200)
            self.assertTrue(r.content.startswith(magic))
        self.assertEqual({f["name"] for f in self.client.get("/api/report/summary").json()["files"]},
                         {docx["name"], xlsx["name"]})

    def test_download_rejects_traversal(self):
        for bad in ("..%2Fsettings.yaml", "x.txt", "нет.docx"):
            self.assertIn(self.client.get(f"/api/report/download/{bad}").status_code, (400, 404))

    # --- задачи ---
    def test_second_job_while_running_is_409(self):
        from api.jobs import JobManager
        jm = JobManager()
        import threading
        gate = threading.Event()
        jm.start("slow", lambda progress: (gate.wait(5), {})[1])
        from api.jobs import JobBusy
        with self.assertRaises(JobBusy):
            jm.start("other", lambda progress: {})
        gate.set()


if __name__ == "__main__":
    unittest.main()
