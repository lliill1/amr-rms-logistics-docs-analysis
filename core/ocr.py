"""OCR для PDF: сканы (страницы-картинки) и изображения внутри страниц с текстом.

Всё работает локально: страницы рисует pypdfium2, текст распознаёт Tesseract (отдельная программа,
как LibreOffice). Если Tesseract не установлен, OCR просто выключен, а остальная обработка идёт как раньше.

Что распознаётся:
  * страница почти без текстового слоя (скан) — целиком, в рендере страницы;
  * страница с текстом, но с картинками (снимки экранов, вставленные таблицы, схемы с подписями) — только
    сами картинки, их содержимое добавляется после текста страницы. Полностраничная картинка под
    нормальным текстовым слоем («searchable PDF») пропускается: текст в ней уже есть.
"""
from __future__ import annotations

import logging
import math
import os
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

TESSERACT_WIN_PATHS = (
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
)
MAX_RENDER_PIXELS = 36_000_000     # больше — снижаем разрешение (большие чертежи A0/A1)
MIN_OCR_SIDE_PX = 1500             # картинки мельче увеличиваем: Tesseract плохо читает мелкий текст
MAX_UPSCALE = 3.0
BLANK_CONTRAST = 24                # разброс яркости меньше — страница пустая, распознавать нечего
# какие вложенные картинки считаем содержательными
MIN_IMAGE_MAX_SIDE_PX = 150
MIN_IMAGE_MIN_SIDE_PX = 40
MIN_IMAGE_PAGE_SHARE = 0.03        # доля площади страницы: мельче — логотип, иконка, линия
FULL_PAGE_SHARE = 0.8              # картинка на всю страницу...
SEARCHABLE_TEXT_CHARS = 400        # ...под таким количеством текста — это скан с готовым текстовым слоем
MIN_IMAGE_TEXT_CHARS = 20          # из картинки меньше символов — шум, не добавляем

Progress = Callable[[int, int], None]


class OcrError(RuntimeError):
    pass


@dataclass
class OcrStatus:
    enabled: bool
    available: bool
    reason: str | None = None        # почему OCR недоступен
    warning: str | None = None       # OCR работает, но не идеально (например, нет английских данных)
    version: str | None = None
    languages: str | None = None     # что реально используется: «rus+eng»

    def to_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class OcrItem:
    page: int                        # номер страницы, с 1
    source: str                      # "page" — страница целиком, "image" — вложенная картинка
    paragraphs: list[str]


# ───────────────────────── Tesseract ─────────────────────────

def _no_window() -> dict:
    """На Windows не показывать мигающее окно консоли на каждый вызов."""
    return {"creationflags": 0x08000000} if sys.platform == "win32" else {}


def find_tesseract(configured: str | None = None) -> str | None:
    if configured and Path(configured).exists():
        return configured
    found = shutil.which("tesseract")
    if found:
        return found
    candidates = list(TESSERACT_WIN_PATHS)
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(str(Path(local) / "Programs" / "Tesseract-OCR" / "tesseract.exe"))
    return next((c for c in candidates if Path(c).exists()), None)


def _run(cmd: list[str], timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, timeout=timeout, **_no_window())


def _probe(exe: str, tessdata_dir: str | None) -> tuple[str, list[str]]:
    """Версия Tesseract и список установленных языков. Старые версии печатают это в stderr, поэтому читаем оба потока."""
    def text_of(res: subprocess.CompletedProcess) -> list[str]:
        return (res.stdout + b"\n" + res.stderr).decode("utf-8", "replace").splitlines()

    first = next((ln.strip() for ln in text_of(_run([exe, "--version"], 30)) if ln.strip()), "tesseract")
    cmd = [exe, "--list-langs"] + (["--tessdata-dir", tessdata_dir] if tessdata_dir else [])
    lines = text_of(_run(cmd, 30))
    start = next((i for i, ln in enumerate(lines) if ln.startswith("List of available languages")), None)
    langs = [ln.strip() for ln in lines[start + 1:] if ln.strip()] if start is not None else []
    return first, langs


_PROBE_CACHE: dict[tuple[str, str], tuple[str, list[str]]] = {}


def _cached_probe(exe: str, tessdata_dir: str | None) -> tuple[str, list[str]]:
    key = (exe, tessdata_dir or "")
    if key not in _PROBE_CACHE:           # неудачные проверки не кэшируем: человек мог доустановить язык
        _PROBE_CACHE[key] = _probe(exe, tessdata_dir)
    return _PROBE_CACHE[key]


def _parse_tsv(tsv: str, min_conf: float) -> list[str]:
    """Абзацы из TSV-вывода Tesseract (строки абзаца разделены \\n). Слова с уверенностью ниже порога — мусор,
    их отбрасываем."""
    paragraphs: dict[tuple, dict[tuple, list[str]]] = {}
    for row in tsv.splitlines()[1:]:
        parts = row.split("\t")
        if len(parts) < 12 or parts[0] != "5":      # уровень 5 — слово
            continue
        try:
            conf = float(parts[10])
        except ValueError:
            continue
        word = parts[11].strip()
        if not word or conf < min_conf:
            continue
        lines = paragraphs.setdefault((parts[2], parts[3]), {})     # (блок, абзац)
        lines.setdefault(parts[4], []).append(word)                 # строка
    out = []
    for lines in paragraphs.values():
        text = "\n".join(" ".join(words) for words in lines.values() if words).strip()
        if text:
            out.append(text)
    return out


class TesseractEngine:
    def __init__(self, exe: str, languages: str, *, dpi: int = 300, tessdata_dir: str | None = None,
                 min_word_conf: float = 30, workers: int = 1, timeout: int = 180,
                 images_in_text_pages: bool = True) -> None:
        self.exe = exe
        self.languages = languages
        self.dpi = dpi
        self.tessdata_dir = tessdata_dir or None
        self.min_word_conf = min_word_conf
        self.workers = max(1, workers)
        self.timeout = timeout
        self.images_in_text_pages = images_in_text_pages

    # --- распознавание одной картинки ---
    def recognize(self, image) -> list[str]:
        """Возвращает абзацы текста. Пустая или однотонная картинка — пустой список без запуска Tesseract."""
        import tempfile

        gray = _prepare(image)
        if gray is None:
            return []
        with tempfile.TemporaryDirectory(prefix="ocr_") as tmp:
            src = Path(tmp) / "page.png"
            gray.save(src, format="PNG", dpi=(self.dpi, self.dpi))
            cmd = [self.exe, str(src), "stdout", "-l", self.languages, "--dpi", str(self.dpi)]
            if self.tessdata_dir:
                cmd += ["--tessdata-dir", self.tessdata_dir]
            cmd.append("tsv")
            env = dict(os.environ, OMP_THREAD_LIMIT="1")   # параллелим по страницам, а не внутри Tesseract
            try:
                res = subprocess.run(cmd, capture_output=True, timeout=self.timeout, env=env, **_no_window())
            except subprocess.TimeoutExpired as e:
                raise OcrError(f"Tesseract не уложился в {self.timeout} с") from e
            if res.returncode != 0:
                msg = res.stderr.decode("utf-8", "replace").strip().splitlines()
                raise OcrError(f"Tesseract завершился с ошибкой: {msg[-1] if msg else res.returncode}")
        return _parse_tsv(res.stdout.decode("utf-8", "replace"), self.min_word_conf)

    def recognize_many(self, images: list) -> list[tuple[list[str], str | None]]:
        """Распознаёт картинки параллельно. Для каждой — (абзацы, текст ошибки или None)."""
        def one(im):
            try:
                return self.recognize(im), None
            except Exception as e:  # noqa: BLE001 — одна плохая страница не должна ронять весь документ
                return [], str(e)

        if self.workers == 1 or len(images) <= 1:
            return [one(im) for im in images]
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            return list(pool.map(one, images))


def _prepare(image):
    """Серое изображение с выровненным контрастом; None, если картинка пустая."""
    from PIL import Image, ImageOps

    gray = image.convert("L")
    lo, hi = gray.getextrema()
    if hi - lo < BLANK_CONTRAST:
        return None
    gray = ImageOps.autocontrast(gray, cutoff=1)
    w, h = gray.size
    longest = max(w, h)
    if longest < MIN_OCR_SIDE_PX:
        k = min(MAX_UPSCALE, MIN_OCR_SIDE_PX / longest)
        gray = gray.resize((round(w * k), round(h * k)), Image.LANCZOS)
    elif w * h > MAX_RENDER_PIXELS:
        k = math.sqrt(MAX_RENDER_PIXELS / (w * h))
        gray = gray.resize((round(w * k), round(h * k)), Image.LANCZOS)
    return gray


# ───────────────────────── настройка и проверка ─────────────────────────

def get_engine(settings) -> tuple[TesseractEngine | None, OcrStatus]:
    """Движок OCR по настройкам (раздел `ocr` в settings.yaml) и понятный статус.
    Движок None, если OCR выключен или чего-то не хватает: причина в статусе."""
    if not settings.get("ocr.enabled", True):
        return None, OcrStatus(enabled=False, available=False, reason="OCR выключен в настройках (ocr.enabled)")
    try:
        import pypdfium2  # noqa: F401
        import PIL  # noqa: F401
    except ImportError as e:
        return None, OcrStatus(True, False, reason=f"не установлена библиотека {e.name}: pip install -r requirements.txt")

    exe = find_tesseract(settings.get("ocr.tesseract_path") or None)
    if exe is None:
        return None, OcrStatus(True, False, reason=(
            "не найден Tesseract OCR. Установите его (Windows: github.com/UB-Mannheim/tesseract/wiki, "
            "при установке отметьте русский язык) или укажите путь в settings.yaml → ocr.tesseract_path"))
    tessdata = str(settings.get("ocr.tessdata_dir") or "") or None
    try:
        version, installed = _cached_probe(exe, tessdata)
    except Exception as e:  # noqa: BLE001
        return None, OcrStatus(True, False, reason=f"Tesseract не запускается ({e})")

    wanted = [x for x in str(settings.get("ocr.languages", "rus+eng")).split("+") if x]
    use = [x for x in wanted if x in installed]
    if not use:
        return None, OcrStatus(True, False, version=version, reason=(
            f"у Tesseract нет языковых данных для «{'+'.join(wanted)}» (установлены: {', '.join(installed) or 'нет'}). "
            "Доустановите языки или поправьте ocr.languages"))
    missing = [x for x in wanted if x not in use]
    warning = f"нет языковых данных: {', '.join(missing)}; распознавание только на {'+'.join(use)}" if missing else None

    workers = int(settings.get("ocr.workers", 0) or 0) or min(4, os.cpu_count() or 1)
    engine = TesseractEngine(
        exe, "+".join(use),
        dpi=int(settings.get("ocr.dpi", 300)),
        tessdata_dir=tessdata,
        min_word_conf=float(settings.get("ocr.min_word_confidence", 30)),
        workers=workers,
        timeout=int(settings.get("ocr.timeout_sec", 180)),
        images_in_text_pages=bool(settings.get("ocr.images_in_text_pages", True)),
    )
    return engine, OcrStatus(True, True, warning=warning, version=version, languages=engine.languages)


# ───────────────────────── PDF ─────────────────────────

def _bounds(obj) -> tuple[float, float, float, float]:
    """Границы объекта на странице (left, bottom, right, top). В pypdfium2 4.x это get_pos, в 5.x — get_bounds."""
    fn = getattr(obj, "get_bounds", None) or obj.get_pos
    return fn()


def _px_size(obj) -> tuple[int, int]:
    fn = getattr(obj, "get_px_size", None) or obj.get_size
    return fn()


def _image_objects(page, text_chars: int) -> list:
    """Содержательные картинки страницы: не значки и не линии, не полностраничная подложка под готовым текстом."""
    import pypdfium2.raw as pdfium_raw

    width, height = page.get_size()
    area = max(width * height, 1.0)
    out = []
    for obj in page.get_objects(filter=[pdfium_raw.FPDF_PAGEOBJ_IMAGE]):
        try:
            left, bottom, right, top = _bounds(obj)
            w_px, h_px = _px_size(obj)
        except Exception:  # noqa: BLE001
            continue
        if max(w_px, h_px) < MIN_IMAGE_MAX_SIDE_PX or min(w_px, h_px) < MIN_IMAGE_MIN_SIDE_PX:
            continue
        share = abs((right - left) * (top - bottom)) / area
        if share < MIN_IMAGE_PAGE_SHARE:
            continue
        if share >= FULL_PAGE_SHARE and text_chars >= SEARCHABLE_TEXT_CHARS:
            continue
        out.append(obj)
    return out


def _render_page(page, dpi: int):
    width, height = page.get_size()
    scale = dpi / 72.0
    if width * height * scale * scale > MAX_RENDER_PIXELS:
        scale = math.sqrt(MAX_RENDER_PIXELS / (width * height))
    return page.render(scale=scale).to_pil()


def ocr_pdf(path: Path, engine: TesseractEngine, text_chars: list[int], min_text_chars: int,
            progress: Progress | None = None) -> tuple[list[OcrItem], list[str]]:
    """Распознаёт страницы-сканы и (если включено) картинки на страницах с текстом.

    text_chars — длина текстового слоя каждой страницы. Возвращает найденный текст и список ошибок.
    """
    import pypdfium2 as pdfium

    items: list[OcrItem] = []
    errors: list[str] = []
    pdf = pdfium.PdfDocument(str(path))
    try:
        # план: какие страницы целиком, у каких — сколько картинок
        plan: list[tuple[int, bool, int]] = []        # (индекс страницы, страница целиком?, число картинок)
        for idx in range(min(len(pdf), len(text_chars))):
            if text_chars[idx] < min_text_chars:
                plan.append((idx, True, 0))
            elif engine.images_in_text_pages:
                page = pdf[idx]
                try:
                    n = len(_image_objects(page, text_chars[idx]))
                finally:
                    page.close()
                if n:
                    plan.append((idx, False, n))
        total = sum(1 if whole else n for _i, whole, n in plan)
        done = 0
        if progress and total:
            progress(0, total)

        batch: list[tuple[int, str, object]] = []     # (страница с 1, источник, картинка)

        def flush() -> None:
            nonlocal done
            if not batch:
                return
            results = engine.recognize_many([im for _p, _s, im in batch])
            for (page_no, source, _im), (paragraphs, err) in zip(batch, results):
                if err:
                    errors.append(f"стр. {page_no}: {err}")
                elif paragraphs:
                    items.append(OcrItem(page_no, source, paragraphs))
            done += len(batch)
            batch.clear()
            if progress:
                progress(done, total)

        limit = engine.workers * 2
        for idx, whole, _n in plan:
            page = pdf[idx]
            try:
                if whole:
                    batch.append((idx + 1, "page", _render_page(page, engine.dpi)))
                else:
                    for obj in _image_objects(page, text_chars[idx]):
                        try:
                            batch.append((idx + 1, "image", obj.get_bitmap(render=False).to_pil()))
                        except Exception as e:  # noqa: BLE001 — экзотический фильтр изображения
                            errors.append(f"стр. {idx + 1}: картинку не удалось прочитать ({e})")
            except Exception as e:  # noqa: BLE001
                errors.append(f"стр. {idx + 1}: {e}")
            finally:
                page.close()
            if len(batch) >= limit:
                flush()
        flush()
    finally:
        pdf.close()
    return items, errors
