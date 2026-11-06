"""Выгрузка отчёта в Word и таблиц в Excel. Оба файла полностью редактируемые."""
from __future__ import annotations

import sqlite3
import shutil
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from docx import Document
from docx.enum.section import WD_ORIENT
from docx.shared import Cm, Pt
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from . import consolidate, diagrams
from .config import ROOT
from .db import rows

TEMPLATE = ROOT / "templates" / "report.docx"


def _src(r: dict) -> str:
    return f"{r['rel_path']}" + (f", с. {r['page']}" if r.get("page") else "")


def _requirements(conn, statuses: tuple[str, ...]) -> list[dict]:
    q = ",".join("?" * len(statuses))
    return rows(conn, f"""SELECT r.*, d.rel_path, d.title AS doc_title FROM requirements r
                          JOIN documents d ON d.id = r.doc_id WHERE r.status IN ({q})
                          ORDER BY r.target, r.category, r.code""", statuses)


def _documents(conn) -> list[dict]:
    return rows(conn, """SELECT d.*, s.rel_path AS superseded_by_path FROM documents d
                         LEFT JOIN documents s ON s.id = d.superseded_by ORDER BY d.relevance DESC, d.rel_path""")


def _table(doc, header: list[str], data: list[list], widths_cm: list[float] | None = None):
    t = doc.add_table(rows=1, cols=len(header))
    t.style = "Table Grid"
    for i, h in enumerate(header):
        cell = t.rows[0].cells[i]
        cell.text = h
        for p in cell.paragraphs:
            for run in p.runs:
                run.bold = True
    for row in data:
        cells = t.add_row().cells
        for i, v in enumerate(row):
            cells[i].text = "" if v is None else str(v)
    if widths_cm:
        for row in t.rows:
            for i, w in enumerate(widths_cm):
                row.cells[i].width = Cm(w)
    for row in t.rows:
        for cell in row.cells:
            for p in cell.paragraphs:
                for run in p.runs:
                    run.font.size = Pt(9)
    return t


def build_docx(conn: sqlite3.Connection, out_dir: Path, include_auto: bool = False, project: str = "") -> Path:
    statuses = ("confirmed", "edited", "auto") if include_auto else ("confirmed", "edited")
    doc = Document(str(TEMPLATE)) if TEMPLATE.exists() else Document()
    sec = doc.sections[0]
    sec.orientation = WD_ORIENT.LANDSCAPE
    sec.page_width, sec.page_height = max(sec.page_width, sec.page_height), min(sec.page_width, sec.page_height)
    for m in ("left_margin", "right_margin"):
        setattr(sec, m, Cm(2))

    docs = _documents(conn)
    reqs = _requirements(conn, statuses)
    questions = rows(conn, "SELECT * FROM open_questions WHERE status != 'closed' ORDER BY id")

    doc.add_heading("Роль AMR/RMS в логистических процессах предприятия", 0)
    doc.add_paragraph(f"Аналитический отчёт по архиву проектной документации{(' · ' + project) if project else ''}")
    doc.add_paragraph(f"Сформирован: {datetime.now():%d.%m.%Y %H:%M}. "
                      + ("Включены неподтверждённые факты (черновик)." if include_auto
                         else "В отчёт включены только факты, подтверждённые аналитиком."))

    # 1. Обзор архива
    doc.add_heading("1. Обзор архива и актуальные документы", 1)
    total = len(docs)
    parsed = sum(1 for d in docs if d["status"] == "parsed")
    relevant = [d for d in docs if d["relevance"] and d["relevance"] > 0 and d["is_current"]]
    outdated = [d for d in docs if not d["is_current"]]
    problems = [d for d in docs if d["status"] in ("needs_ocr", "unsupported", "error")]
    doc.add_paragraph(f"Всего файлов: {total}; с извлечённым текстом: {parsed}; актуальных и относящихся к AMR/RMS: "
                      f"{len(relevant)}; устаревших редакций: {len(outdated)}; требуют ручной обработки: {len(problems)}.")
    doc.add_heading("1.1. Документы, значимые для AMR/RMS", 2)
    _table(doc, ["Документ", "Тип", "Дата", "Ред.", "Статус", "Сторона", "Подсистемы", "Релев."],
           [[d["rel_path"], d["doc_type"], d["doc_date"], d["revision"], d["approval"], d["side"], d["subsystems"], d["relevance"]]
            for d in relevant], [8, 3.5, 2, 1.3, 2.2, 2.2, 3.5, 1.3])
    if outdated:
        doc.add_heading("1.2. Устаревшие редакции", 2)
        _table(doc, ["Документ", "Заменён документом"], [[d["rel_path"], d["superseded_by_path"] or "—"] for d in outdated], [12, 12])
    if problems:
        doc.add_heading("1.3. Документы, требующие ручной обработки", 2)
        reason = {"needs_ocr": "скан, нужен OCR", "unsupported": "формат не поддерживается", "error": "ошибка чтения"}
        _table(doc, ["Документ", "Причина"], [[d["rel_path"], reason.get(d["status"], d["status"]) + (f": {d['error']}" if d["error"] else "")]
                                              for d in problems], [12, 12])

    # 2–4. Логистика, процессы, системы (версия 2)
    _logistics_sections(doc, conn, statuses, include_auto, out_dir)

    # 5. Требования
    doc.add_heading("5. Сводная таблица функций и требований к AMR/RMS", 1)
    _responsibility_section(doc, conn, include_auto)
    doc.add_heading("5.2. Требования к AMR и RMS", 2)
    if not reqs:
        doc.add_paragraph("Подтверждённых требований пока нет.")
    for target in ("AMR", "RMS", "AMR+RMS", "Другое"):
        part = [r for r in reqs if r["target"] == target]
        if not part:
            continue
        doc.add_heading({"AMR": "Требования к роботам (AMR)", "RMS": "Требования к системе управления роботами (RMS)",
                         "AMR+RMS": "Общие требования к AMR и RMS", "Другое": "Требования к смежным системам и инфраструктуре"}[target], 2)
        _table(doc, ["ID", "Требование", "Категория", "Обязательность", "Источник", "Цитата"],
               [[r["code"], r["text"], r["category"], r["obligation"], _src(r), r["quote"]] for r in part],
               [1.6, 7, 2.5, 2.3, 5, 6.5])

    # 6. Вопросы
    doc.add_heading("6. Противоречия и открытые вопросы", 1)
    conflicts = consolidate.refresh(conn)
    for kind, heading in (("contradiction", "6.1. Противоречия между документами"),
                          ("revision", "6.2. Изменения между редакциями"), ("gap", "6.3. Пробелы: решения не найдены")):
        part = [c for c in conflicts if c["kind"] == kind and c["status"] != "dismissed"]
        if not part:
            continue
        doc.add_heading(heading, 2)
        _table(doc, ["№", "Суть", "Варианты и источники", "Статус"],
               [[c["id"], c["title"], _variants_text(c) or "—",
                 c["status_label"] + (f": {c['resolution']}" if c.get("resolution") else "")] for c in part],
               [1, 8, 12, 3])
    doc.add_heading("6.4. Вопросы заказчику", 2)
    if not questions:
        doc.add_paragraph("Открытых вопросов пока нет.")
    else:
        _table(doc, ["№", "Вопрос", "Контекст", "Источники", "Статус"],
               [[q["id"], q["text"], q["context"], q["sources"], q["status"]] for q in questions], [1, 9, 6, 6, 2])

    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"Отчёт_AMR_RMS_{datetime.now():%Y%m%d_%H%M}.docx"
    doc.save(str(path))
    return path


def build_xlsx(conn: sqlite3.Connection, out_dir: Path) -> Path:
    wb = Workbook()
    sheets = [
        ("Документы", ["ID", "Путь", "Название", "Тип", "Дата", "Редакция", "Статус документа", "Сторона", "Подсистемы",
                       "Релевантность", "Актуальный", "Состояние обработки"],
         [[d["id"], d["rel_path"], d["title"], d["doc_type"], d["doc_date"], d["revision"], d["approval"], d["side"],
           d["subsystems"], d["relevance"], "да" if d["is_current"] else "нет", d["status"]] for d in _documents(conn)]),
        ("Требования", ["ID", "Требование", "Адресат", "Категория", "Обязательность", "Источник", "Страница", "Цитата", "Статус ревью", "Комментарий"],
         [[r["code"], r["text"], r["target"], r["category"], r["obligation"], r["rel_path"], r["page"], r["quote"], r["status"], r["comment"]]
          for r in _requirements(conn, ("auto", "confirmed", "edited", "rejected"))]),
        ("Процессы", ["ID", "Процесс", "Роль AMR", "Триггер", "Инициатор", "Откуда", "Куда", "Груз", "Шаги",
                      "Источник", "Страница", "Цитата", "Статус ревью", "Комментарий"],
         [[p["code"], p["name"], p["amr_role"], p["trigger"], p["initiator"], p["from_location"], p["to_location"], p["cargo"],
           "\n".join(f"{i}. {st.get('actor')}: {st['action']}" for i, st in enumerate(diagrams.process_steps(p), start=1)),
           p["rel_path"], p["page"], p["quote"], p["status"], p["comment"]]
          for p in rows(conn, "SELECT p.*, d.rel_path FROM processes p JOIN documents d ON d.id = p.doc_id ORDER BY p.code")]),
        ("Обмены", ["ID", "Откуда", "Куда", "Что передаётся", "Протокол", "Когда", "Источник", "Страница", "Цитата",
                    "Статус ревью", "Комментарий"],
         [[i["code"], i["source_system"], i["target_system"], i["message"], i["protocol"], i["trigger"], i["rel_path"], i["page"],
           i["quote"], i["status"], i["comment"]]
          for i in rows(conn, "SELECT i.*, d.rel_path FROM interfaces i JOIN documents d ON d.id = i.doc_id ORDER BY i.code")]),
        _matrix_sheet(conn),
        ("Противоречия", ["№", "Вид", "Суть", "Варианты и источники", "Статус", "Решение"],
         [[c["id"], c["kind_label"], c["title"], _variants_text(c), c["status_label"], c["resolution"]]
          for c in consolidate.refresh(conn)]),
        ("Открытые вопросы", ["№", "Вопрос", "Контекст", "Источники", "Статус", "Ответ"],
         [[q["id"], q["text"], q["context"], q["sources"], q["status"], q["answer"]] for q in rows(conn, "SELECT * FROM open_questions ORDER BY id")]),
    ]
    wb.remove(wb.active)
    head_fill = PatternFill("solid", fgColor="DCE6F5")
    for name, header, data in sheets:
        ws = wb.create_sheet(name)
        ws.append(header)
        for c in ws[1]:
            c.font = Font(bold=True)
            c.fill = head_fill
        for row in data:
            ws.append(row)
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions
        for i, h in enumerate(header, start=1):
            width = max([len(str(h))] + [len(str(r[i - 1] or "")) for r in data[:200]])
            ws.column_dimensions[get_column_letter(i)].width = min(max(10, width + 2), 60)
        for row in ws.iter_rows(min_row=2):
            for c in row:
                c.alignment = Alignment(wrap_text=True, vertical="top")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"Таблицы_AMR_RMS_{datetime.now():%Y%m%d_%H%M}.xlsx"
    wb.save(str(path))
    return path


ROLE_ORDER = {"роботизирован": 0, "планируется": 1, "обсуждается": 2, "вручную": 3}


def _picture(doc, d, out_dir: Path) -> None:
    """Вставляет схему картинкой; без Pillow — ссылка на файлы схем."""
    if d.empty:
        doc.add_paragraph(d.note or "Нет данных для схемы.")
        return
    tmp = Path(tempfile.mkdtemp(prefix="dg_"))
    try:
        png = diagrams.to_png(d, tmp / f"{d.key}.png")
        if png:
            sec = doc.sections[-1]
            width = sec.page_width - sec.left_margin - sec.right_margin
            doc.add_picture(str(png), width=min(width, Cm(d.width / 40)))
        else:
            doc.add_paragraph("[Схема — в архиве «Схемы» (draw.io, SVG): для картинки в Word установите пакет Pillow.]")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if d.note:
        p = doc.add_paragraph(d.note)
        p.runs[0].italic = True


def _logistics_sections(doc, conn, statuses, include_auto: bool, out_dir: Path) -> None:
    q = ",".join("?" * len(statuses))
    procs = rows(conn, f"""SELECT p.*, d.rel_path FROM processes p JOIN documents d ON d.id = p.doc_id
                           WHERE p.status IN ({q}) AND d.is_current = 1 ORDER BY p.code""", statuses)
    ifs = rows(conn, f"""SELECT i.*, d.rel_path FROM interfaces i JOIN documents d ON d.id = i.doc_id
                         WHERE i.status IN ({q}) AND d.is_current = 1 ORDER BY i.source_system, i.target_system, i.code""", statuses)

    doc.add_heading("2. Общая схема логистики", 1)
    doc.add_paragraph("Основные материальные потоки между участками. Фиолетовым выделены потоки и участки, "
                      "где перевозку выполняют или планируют выполнять роботы AMR.")
    _picture(doc, diagrams.logistics_diagram(conn, include_auto), out_dir)
    flows = [p for p in procs if p["from_location"] or p["to_location"]]
    if flows:
        _table(doc, ["ID", "Поток", "Откуда", "Куда", "Груз", "Роль AMR", "Источник"],
               [[p["code"], p["name"], p["from_location"] or "—", p["to_location"] or "—", p["cargo"] or "—",
                 p["amr_role"], _src(p)] for p in sorted(flows, key=lambda p: ROLE_ORDER.get(p["amr_role"], 9))],
               [1.6, 6, 4, 4, 3, 2.6, 5])

    doc.add_heading("3. Карта процессов с участием AMR", 1)
    robot = [p for p in procs if p["amr_role"] != "вручную"]
    if not robot:
        doc.add_paragraph("Процессов с участием роботов пока не выявлено (или они не подтверждены).")
    for p in sorted(robot, key=lambda p: (ROLE_ORDER.get(p["amr_role"], 9), p["code"])):
        doc.add_heading(f"{p['code']}. {p['name']}", 2)
        facts = [("Роль роботов", p["amr_role"]), ("Триггер", p["trigger"]), ("Инициатор", p["initiator"]),
                 ("Откуда → куда", " → ".join(x for x in (p["from_location"], p["to_location"]) if x)),
                 ("Груз", p["cargo"]), ("Источник", _src(p))]
        _table(doc, ["Параметр", "Значение"], [[k, v or "—"] for k, v in facts], [5, 19])
        steps = diagrams.process_steps(p)
        if steps:
            doc.add_paragraph("Последовательность операций:")
            for i, st in enumerate(steps, start=1):
                try:
                    doc.add_paragraph(f"{st.get('actor')}: {st['action']}", style="List Number")
                except KeyError:   # в пользовательском шаблоне может не быть стиля нумерованного списка
                    doc.add_paragraph(f"{i}. {st.get('actor')}: {st['action']}")
            if len(steps) > 1:
                _picture(doc, diagrams.process_diagram(p), out_dir)

    doc.add_heading("4. Схема взаимодействия систем", 1)
    doc.add_paragraph("Информационные обмены между SAP EWM, ERP, MES, RMS, роботами и автоматикой в части роботизированных перевозок.")
    _picture(doc, diagrams.systems_diagram(conn, include_auto), out_dir)
    if ifs:
        _table(doc, ["ID", "Откуда", "Куда", "Что передаётся", "Протокол", "Когда", "Источник"],
               [[i["code"], i["source_system"], i["target_system"], i["message"], i["protocol"] or "не указан",
                 i["trigger"] or "—", _src(i)] for i in ifs], [1.6, 2.6, 2.6, 6, 3, 4, 5])


def _responsibility_section(doc, conn, include_auto: bool) -> None:
    m = consolidate.responsibility_matrix(conn, include_auto)
    doc.add_heading("5.1. Функции и границы ответственности", 2)
    doc.add_paragraph("Какая система выполняет каждую функцию роботизированной перевозки по актуальным документам. "
                      "«?» — в документах не указано, «!» — документы противоречат друг другу.")
    systems = m["systems"]
    data = []
    for r in m["rows"]:
        line = [r["title"] + (" *" if r["required"] else "")]
        for sname in systems:
            cell = r["cells"].get(sname, [])
            cur = [x for x in cell if x["is_current"]]
            line.append(("!" if r["state"] == "conflict" else "●") + " " + ", ".join(x["code"] for x in cur) if cur
                        else ("○" if cell else ""))
        line.append({"ok": "определено", "conflict": "! противоречие", "gap": "? не указано",
                     "outdated": "только в старых редакциях", "empty": "—"}[r["state"]])
        data.append(line)
    _table(doc, ["Функция"] + systems + ["Итог"], data)
    doc.add_paragraph("* — обязательная функция. ● — указано в актуальном документе (коды фактов), ○ — только в устаревшей редакции.")


def _variants_text(c: dict) -> str:
    det = c["details"]
    parts = []
    for v in det.get("variants", []):
        srcs = "; ".join(f"{s['label']}" + ("" if s["is_current"] else " (устар.)") for s in v["sources"])
        parts.append(f"{v['value']}: {srcs}")
    if not parts and det.get("sources"):
        parts.append("; ".join(s["label"] for s in det["sources"]))
    return "\n".join(parts)


def build_diagrams_zip(conn: sqlite3.Connection, out_dir: Path, include_auto: bool = True) -> Path:
    """Все схемы в редактируемом виде: один .drawio (страница на схему), SVG, Mermaid и PNG."""
    ds = [d for d in diagrams.all_diagrams(conn, include_auto) if not d.empty]
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"Схемы_AMR_RMS_{datetime.now():%Y%m%d_%H%M}.zip"
    tmp = Path(tempfile.mkdtemp(prefix="dg_"))
    try:
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("Схемы_AMR_RMS.drawio", diagrams.to_drawio(ds))
            for i, d in enumerate(ds, start=1):
                base = f"{i:02d}_{d.key}"
                z.writestr(f"svg/{base}.svg", diagrams.to_svg(d, uid=f"d{i}", with_title=True))
                z.writestr(f"mermaid/{base}.mmd", diagrams.to_mermaid(d))
                png = diagrams.to_png(d, tmp / f"{base}.png")
                if png:
                    z.write(png, f"png/{base}.png")
            z.writestr("README.txt", "Схемы DocAnalyzer AMR/RMS\n\n"
                       "Схемы_AMR_RMS.drawio — откройте в diagrams.net (draw.io) или в VS Code с расширением Draw.io Integration.\n"
                       "svg/ — для вставки в документы и презентации.\n"
                       "mermaid/ — текстовое описание схем (Confluence, GitLab, Markdown).\n"
                       "png/ — картинки.\n")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return path


def _matrix_sheet(conn) -> tuple[str, list, list]:
    m = consolidate.responsibility_matrix(conn, include_auto=True)
    v = {f.key: f for f in consolidate.load_vocab().functions}
    data = []
    for r in m["rows"]:
        line = [r["title"], "да" if v[r["key"]].required else "нет"]
        for sname in m["systems"]:
            line.append(", ".join(x["code"] + ("" if x["is_current"] else " (устар.)") for x in r["cells"].get(sname, [])))
        line.append({"ok": "определено", "conflict": "противоречие", "gap": "не указано",
                     "outdated": "только в старых редакциях", "empty": "—"}[r["state"]])
        data.append(line)
    return "Ответственность", ["Функция", "Обязательная"] + m["systems"] + ["Итог"], data
