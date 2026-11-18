"""OCR сканов и картинок в PDF.

Тесты логики идут с поддельным движком (без Tesseract). Тесты с настоящим Tesseract пропускаются,
если он или русские данные к нему не установлены. Сборка PDF с текстом и картинкой — через reportlab
(нет reportlab — эти тесты пропускаются).

Запуск:  python -m pytest -q tests/test_ocr.py
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "scripts", Path(__file__).resolve().parent):
    sys.path.insert(0, str(p))

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from core import db, ocr  # noqa: E402
from core.config import load_settings  # noqa: E402
from core.ocr import OcrStatus, _parse_tsv  # noqa: E402
from core.parsers.pdf_parser import parse_pdf  # noqa: E402
from core.pipeline import OCR_EMPTY_MARK, build_index, ingest_source  # noqa: E402

try:
    import reportlab  # noqa: F401
    HAVE_REPORTLAB = True
except ImportError:
    HAVE_REPORTLAB = False

FONT = next((Path(p) for p in ("C:/Windows/Fonts/arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
             if Path(p).exists()), None)

SCAN_LINES_1 = ["2.1 Требования к роботам",
                "Робот должен автоматически строить маршрут к зоне погрузки.",
                "Система передаёт роботу транспортное задание по протоколу обмена.",
                "Время доставки паллеты не должно превышать двенадцать минут."]
SCAN_LINES_2 = ["2.2 Интеграция с учётной системой",
                "Учётная система создаёт транспортное задание и отправляет его в систему роботов.",
                "Статус выполнения возвращается обратно через интерфейс."]


def text_image(lines, size=(1240, 1754), fs=30, left=100, top=120) -> Image.Image:
    im = Image.new("RGB", size, "white")
    d, f = ImageDraw.Draw(im), ImageFont.truetype(str(FONT), fs)
    y = top
    for line in lines:
        d.text((left, y), line, fill="black", font=f)
        y += fs + 18
    return im


def make_scan_pdf(path: Path) -> Path:
    """Скан на три страницы: две с текстом и одна пустая."""
    p1, p2 = text_image(SCAN_LINES_1), text_image(SCAN_LINES_2)
    blank = Image.new("RGB", (1240, 1754), "white")
    p1.save(path, "PDF", resolution=150, save_all=True, append_images=[p2, blank])
    return path


def make_mixed_pdf(path: Path, body_lines: list[str], image: Image.Image, full_page: bool = False) -> Path:
    """Страница с настоящим текстовым слоем и вставленной картинкой."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas

    pdfmetrics.registerFont(TTFont("Cyr", str(FONT)))
    shot = path.with_suffix(".png")
    image.save(shot)
    c = canvas.Canvas(str(path), pagesize=A4)
    c.setFont("Cyr", 10)
    y = 810
    for line in body_lines:
        c.drawString(40, y, line)
        y -= 13
    if full_page:
        c.drawImage(str(shot), 0, 0, width=A4[0], height=A4[1])
    else:
        c.drawImage(str(shot), 50, 450, width=400, height=153)
    c.showPage()
    c.save()
    return path


class FakeEngine:
    """Движок вместо Tesseract: возвращает заранее заданные абзацы и считает вызовы."""
    images_in_text_pages = True
    workers = 1
    dpi = 100

    def __init__(self, paragraphs=("Распознанный абзац про транспортное задание.",), images_in_text_pages=True):
        self.paragraphs = list(paragraphs)
        self.images_in_text_pages = images_in_text_pages
        self.calls = 0

    def recognize_many(self, images):
        self.calls += len(images)
        return [(list(self.paragraphs), None) for _ in images]


@unittest.skipIf(FONT is None, "нет шрифта с кириллицей")
class ParsePdfWithFakeEngineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_scan_without_ocr_is_flagged_as_before(self):
        doc = parse_pdf(make_scan_pdf(self.dir / "scan.pdf"))
        self.assertTrue(doc.needs_ocr)
        self.assertEqual((doc.blocks, doc.ocr_pages), ([], 0))

    def test_scan_with_ocr_becomes_text(self):
        engine = FakeEngine(["2.1 Требования к роботам", "Робот должен строить маршрут к зоне погрузки."])
        progress = []
        doc = parse_pdf(make_scan_pdf(self.dir / "scan.pdf"), ocr=engine, ocr_progress=lambda d, t: progress.append((d, t)))
        self.assertFalse(doc.needs_ocr)
        self.assertEqual(engine.calls, 3)                        # все три страницы без текстового слоя
        self.assertEqual(doc.ocr_pages, 3)
        self.assertEqual({b.page for b in doc.blocks}, {1, 2, 3})
        heads = [b for b in doc.blocks if b.kind == "heading"]
        self.assertEqual(heads[0].text, "2.1 Требования к роботам")
        self.assertEqual(progress[-1], (3, 3))

    def test_scan_with_empty_recognition_stays_needs_ocr(self):
        doc = parse_pdf(make_scan_pdf(self.dir / "scan.pdf"), ocr=FakeEngine(paragraphs=[]))
        self.assertTrue(doc.needs_ocr)
        self.assertTrue(doc.meta["ocr_attempted"])
        self.assertEqual(doc.ocr_pages, 0)

    def test_engine_failure_does_not_break_document(self):
        class Broken(FakeEngine):
            def recognize_many(self, images):
                return [([], "Tesseract упал") for _ in images]

        doc = parse_pdf(make_scan_pdf(self.dir / "scan.pdf"), ocr=Broken())
        self.assertTrue(doc.needs_ocr)
        self.assertIn("Tesseract упал", doc.meta["ocr_errors"][0])

    @unittest.skipUnless(HAVE_REPORTLAB, "нет reportlab")
    def test_image_inside_text_page_is_recognized_after_page_text(self):
        pdf = make_mixed_pdf(self.dir / "mixed.pdf", ["3.2 Транспортные задания", "Ниже приведён снимок экрана системы управления роботами."],
                             text_image(["Требование: доставка за двенадцать минут"], size=(1100, 420), fs=34, left=30, top=20))
        engine = FakeEngine(["Текст со снимка экрана: доставка за двенадцать минут."])
        doc = parse_pdf(pdf, ocr=engine)
        texts = [b.text for b in doc.blocks]
        self.assertEqual(engine.calls, 1)
        self.assertEqual(texts[0], "3.2 Транспортные задания")                  # текстовый слой не тронут
        self.assertEqual(texts[-1], "Текст со снимка экрана: доставка за двенадцать минут.")
        self.assertEqual(doc.ocr_pages, 1)
        self.assertFalse(doc.needs_ocr)

    @unittest.skipUnless(HAVE_REPORTLAB, "нет reportlab")
    def test_images_in_text_pages_can_be_switched_off(self):
        pdf = make_mixed_pdf(self.dir / "mixed.pdf", ["3.2 Транспортные задания", "Ниже приведён снимок экрана системы управления роботами."],
                             text_image(["Требование"], size=(1100, 420), fs=34, left=30, top=20))
        engine = FakeEngine(images_in_text_pages=False)
        doc = parse_pdf(pdf, ocr=engine)
        self.assertEqual(engine.calls, 0)
        self.assertEqual(doc.ocr_pages, 0)

    @unittest.skipUnless(HAVE_REPORTLAB, "нет reportlab")
    def test_fullpage_image_under_real_text_is_skipped(self):
        """«Searchable PDF»: картинка скана на всю страницу и готовый текстовый слой — повторно не распознаём."""
        body = [f"Строка {i}: транспортное задание передаётся из системы в систему роботов." for i in range(1, 12)]
        pdf = make_mixed_pdf(self.dir / "searchable.pdf", body, text_image(body[:3]), full_page=True)
        engine = FakeEngine()
        doc = parse_pdf(pdf, ocr=engine)
        self.assertEqual(engine.calls, 0)
        self.assertEqual(doc.ocr_pages, 0)
        self.assertTrue(any("Строка 5" in b.text for b in doc.blocks))

    @unittest.skipUnless(HAVE_REPORTLAB, "нет reportlab")
    def test_tiny_images_are_ignored(self):
        pdf = make_mixed_pdf(self.dir / "logo.pdf", ["3.2 Транспортные задания", "Текст страницы с небольшим логотипом."],
                             Image.new("RGB", (60, 20), "black"))
        engine = FakeEngine()
        parse_pdf(pdf, ocr=engine)
        self.assertEqual(engine.calls, 0)


class BlankPageTest(unittest.TestCase):
    def test_blank_image_does_not_start_tesseract(self):
        engine = ocr.TesseractEngine("/нет/такого/tesseract", "rus")
        with mock.patch("core.ocr.subprocess.run", side_effect=AssertionError("Tesseract не должен запускаться")):
            self.assertEqual(engine.recognize(Image.new("RGB", (800, 600), "white")), [])

    def test_tesseract_failure_is_reported_per_page(self):
        engine = ocr.TesseractEngine("/нет/такого/tesseract", "rus", workers=2)
        noisy = Image.effect_noise((400, 400), 80).convert("RGB")
        with mock.patch("core.ocr.subprocess.run", side_effect=FileNotFoundError("нет файла")):
            (paragraphs, err), = engine.recognize_many([noisy])
        self.assertEqual(paragraphs, [])
        self.assertIn("нет файла", err)


class TsvTest(unittest.TestCase):
    HEAD = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"

    def row(self, block, par, line, word, conf, text):
        return f"5\t1\t{block}\t{par}\t{line}\t{word}\t0\t0\t10\t10\t{conf}\t{text}"

    def test_paragraphs_lines_and_confidence(self):
        tsv = "\n".join([
            self.HEAD,
            "4\t1\t1\t1\t1\t0\t0\t0\t10\t10\t-1\t",                    # строка, не слово — игнорируется
            self.row(1, 1, 1, 1, 96.5, "Робот"), self.row(1, 1, 1, 2, 91, "должен"),
            self.row(1, 1, 2, 1, 88, "ехать."), self.row(1, 1, 2, 2, 12, "@#~"),   # мусор с низкой уверенностью
            self.row(2, 1, 1, 1, 90, "Второй"), self.row(2, 1, 1, 2, 90, "абзац"),
            self.row(3, 1, 1, 1, -1, ""),                              # пустое слово
        ])
        self.assertEqual(_parse_tsv(tsv, 30), ["Робот должен\nехать.", "Второй абзац"])
        self.assertEqual(_parse_tsv(tsv, 0)[0], "Робот должен\nехать. @#~")


class PipelineOcrTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        tmp = Path(self.tmp.name)
        self.archive = tmp / "archive"
        self.archive.mkdir()
        make_scan_pdf(self.archive / "Скан_протокола.pdf") if FONT else None
        self.settings = load_settings({"paths": {"data_dir": str(tmp / "data"), "output_dir": str(tmp / "out"),
                                                 "db_file": str(tmp / "data" / "t.sqlite")}})
        self.conn = db.connect(self.settings.db_file)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def run_ingest(self, engine, status):
        with mock.patch("core.pipeline.ocr.get_engine", return_value=(engine, status)):
            return ingest_source(self.conn, self.settings, self.archive)

    def doc(self):
        return db.one(self.conn, "SELECT * FROM documents")

    UNAVAILABLE = OcrStatus(True, False, reason="не найден Tesseract OCR")
    AVAILABLE = OcrStatus(True, True, version="fake", languages="rus+eng")

    @unittest.skipIf(FONT is None, "нет шрифта с кириллицей")
    def test_scan_without_tesseract_then_reprocessed_after_install(self):
        s1 = self.run_ingest(None, self.UNAVAILABLE)
        self.assertEqual((s1.needs_ocr, s1.parsed, s1.ocr_docs), (1, 0, 0))
        self.assertEqual(self.doc()["status"], "needs_ocr")
        self.assertTrue(any("не найден Tesseract OCR" in m for m in s1.messages), s1.messages)

        # OCR подключили: тот же файл читается заново, а не считается повтором
        engine = FakeEngine(["2.1 Требования к роботам", "Робот должен строить маршрут к зоне погрузки."])
        s2 = self.run_ingest(engine, self.AVAILABLE)
        self.assertEqual((s2.reprocessed, s2.duplicates, s2.added), (1, 0, 0))
        self.assertEqual((s2.parsed, s2.ocr_docs, s2.ocr_pages, s2.needs_ocr), (1, 1, 3, 0))
        d = self.doc()
        self.assertEqual((d["status"], d["ocr_pages"], d["error"]), ("parsed", 3, None))
        self.assertEqual(db.one(self.conn, "SELECT count(*) n FROM documents")["n"], 1)
        self.assertGreater(db.one(self.conn, "SELECT count(*) n FROM blocks")["n"], 0)

        # дальше поисковый индекс строится как для обычного документа
        self.assertGreater(build_index(self.conn, self.settings), 0)

        # третий запуск: файл уже прочитан — обычный повтор
        s3 = self.run_ingest(engine, self.AVAILABLE)
        self.assertEqual((s3.reprocessed, s3.duplicates), (0, 1))

    @unittest.skipIf(FONT is None, "нет шрифта с кириллицей")
    def test_scan_with_no_text_is_not_reocred_every_run(self):
        engine = FakeEngine(paragraphs=[])
        s1 = self.run_ingest(engine, self.AVAILABLE)
        self.assertEqual(s1.needs_ocr, 1)
        self.assertEqual(self.doc()["error"], OCR_EMPTY_MARK)
        calls = engine.calls
        s2 = self.run_ingest(engine, self.AVAILABLE)
        self.assertEqual((s2.reprocessed, s2.duplicates), (0, 1))
        self.assertEqual(engine.calls, calls)

    def test_old_database_gets_ocr_pages_column(self):
        old = Path(self.tmp.name) / "old.sqlite"
        raw = sqlite3.connect(old)
        raw.execute("CREATE TABLE documents (id INTEGER PRIMARY KEY, source_path TEXT NOT NULL, rel_path TEXT NOT NULL, "
                    "sha256 TEXT NOT NULL UNIQUE, ext TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'new', pages INTEGER)")
        raw.commit()
        raw.close()
        conn = db.connect(old)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(documents)")}
        conn.close()
        self.assertIn("ocr_pages", cols)


class EngineSetupTest(unittest.TestCase):
    def test_disabled_in_settings(self):
        engine, st = ocr.get_engine(load_settings({"ocr": {"enabled": False}}))
        self.assertIsNone(engine)
        self.assertFalse(st.available)

    def test_missing_tesseract_is_explained(self):
        with mock.patch("core.ocr.find_tesseract", return_value=None):
            engine, st = ocr.get_engine(load_settings())
        self.assertIsNone(engine)
        self.assertIn("Tesseract", st.reason)

    def test_missing_language_data(self):
        with mock.patch("core.ocr.find_tesseract", return_value="/x/tesseract"), \
                mock.patch("core.ocr._cached_probe", return_value=("tesseract 5", ["eng", "osd"])):
            engine, st = ocr.get_engine(load_settings({"ocr": {"languages": "rus"}}))
        self.assertIsNone(engine)
        self.assertIn("rus", st.reason)

    def test_partial_language_data_still_works_with_warning(self):
        with mock.patch("core.ocr.find_tesseract", return_value="/x/tesseract"), \
                mock.patch("core.ocr._cached_probe", return_value=("tesseract 5", ["rus", "osd"])):
            engine, st = ocr.get_engine(load_settings())          # по умолчанию rus+eng
        self.assertEqual(engine.languages, "rus")
        self.assertTrue(st.available)
        self.assertIn("eng", st.warning)


_REAL, _REAL_STATUS = ocr.get_engine(load_settings())


@unittest.skipIf(FONT is None, "нет шрифта с кириллицей")
@unittest.skipIf(_REAL is None, f"Tesseract недоступен: {_REAL_STATUS.reason}")
class RealTesseractTest(unittest.TestCase):
    """Настоящее распознавание. Допускаем ошибки отдельных букв, проверяем смысловые слова."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_scanned_pdf(self):
        doc = parse_pdf(make_scan_pdf(self.dir / "scan.pdf"), ocr=_REAL)
        text = doc.full_text.lower()
        self.assertFalse(doc.needs_ocr)
        self.assertEqual(doc.ocr_pages, 2)
        for word in ("маршрут", "транспортное задание", "паллеты", "интерфейс"):
            self.assertIn(word, text)
        self.assertTrue(any(b.kind == "heading" and b.page == 2 for b in doc.blocks))

    @unittest.skipUnless(HAVE_REPORTLAB, "нет reportlab")
    def test_picture_inside_pdf(self):
        pdf = make_mixed_pdf(self.dir / "mixed.pdf", ["3.2 Транспортные задания", "Ниже приведён снимок экрана системы."],
                             text_image(["Требование: робот должен доставлять", "паллету на участок сборки", "за двенадцать минут."],
                                        size=(1100, 420), fs=34, left=30, top=20))
        doc = parse_pdf(pdf, ocr=_REAL)
        self.assertEqual(doc.blocks[0].text, "3.2 Транспортные задания")
        tail = " ".join(b.text for b in doc.blocks[2:]).lower()
        for word in ("паллету", "сборки", "минут"):
            self.assertIn(word, tail)


if __name__ == "__main__":
    unittest.main()
