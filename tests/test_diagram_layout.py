"""Читаемость схем: на объёме, близком к настоящему архиву, ничего не накладывается и не обрезается.

Раньше на таких данных подписи связей ложились друг на друга и на блоки, схема логистики разъезжалась
до 3700 px в ширину, а текст шагов процесса обрезался многоточием.

Запуск:  python -m pytest -q tests/test_diagram_layout.py
"""
from __future__ import annotations

import json
import math
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import db, diagrams  # noqa: E402
from core.diagrams import BADGE_R, _rect, _seg_hits_rect, _segs  # noqa: E402

EDGES = [("ERP", "SAP EWM", "заказ на поставку", "IDoc"), ("SAP EWM", "ERP", "подтверждение движения товара", "IDoc"),
         ("MES", "SAP EWM", "заказ на производство", "REST"), ("SAP EWM", "RMS", "транспортное задание", "REST"),
         ("RMS", "SAP EWM", "статус выполнения", "REST"), ("RMS", "AMR", "маршрут и команда на выполнение", "VDA 5050"),
         ("AMR", "RMS", "состояние и позиция робота", "VDA 5050"), ("RMS", "PLC / автоматика", "команда на открытие ворот", "OPC UA"),
         ("PLC / автоматика", "RMS", "подтверждение положения ворот", "OPC UA"), ("Оператор", "RMS", "вызов робота с терминала", None),
         ("RMS", "Оператор", "уведомление о прибытии робота", None), ("MES", "RMS", "приоритет заказа на подвоз", None),
         ("WMS", "SAP EWM", "остатки на складе", "REST"), ("ERP", "MES", "производственный заказ", "IDoc"),
         ("RMS", "WMS", "отчёт о перемещениях", None)]
PLACES = ["Зона приёмки", "Зона хранения", "Зона комплектации склада", "Буферная зона", "Участок сборки 1", "Участок сборки 2",
          "Участок сварки", "Участок покраски", "Зона отгрузки", "Зона возврата тары"]
FLOWS = [(0, 1), (1, 2), (2, 3), (3, 4), (3, 5), (5, 6), (6, 7), (7, 8), (8, 9), (9, 0), (2, 4), (4, 8), (1, 9), (6, 3)]   # есть цикл
STEPS = [("Оператор", "Оператор участка сборки вызывает подвоз комплектующих на терминале и выбирает номенклатуру"),
         ("SAP EWM", "SAP EWM создаёт складскую задачу и резервирует количество на складе комплектации"),
         ("MES", "MES проверяет наличие производственного заказа и подтверждает потребность участка"),
         ("RMS", "RMS назначает свободного робота с учётом заряда батареи и текущей загрузки парка"),
         ("AMR", "Робот забирает тележку в зоне комплектации склада и доставляет её в зону подачи участка сборки"),
         ("PLC / автоматика", "Ворота открываются по команде RMS и подтверждают положение"),
         ("AMR", "Робот сообщает о прибытии и ожидает разгрузки оператором"),
         ("Оператор", "Оператор подтверждает разгрузку на терминале"),
         ("RMS", "RMS фиксирует завершение и передаёт статус в SAP EWM"),
         ("SAP EWM", "SAP EWM закрывает складскую задачу и обновляет остатки")]


def build_db() -> tuple[tempfile.TemporaryDirectory, object]:
    tmp = tempfile.TemporaryDirectory()
    conn = db.connect(Path(tmp.name) / "s.sqlite")
    conn.execute("INSERT INTO documents(id,source_path,rel_path,sha256,ext,status,is_current) VALUES (1,'x','x.docx','h','.docx','parsed',1)")
    for i, (a, b, m, p) in enumerate(EDGES, 1):
        conn.execute("INSERT INTO interfaces(code,doc_id,source_system,target_system,message,protocol,status) VALUES (?,?,?,?,?,?,'confirmed')",
                     (f"IF-{i:03}", 1, a, b, m, p))
    for i, (a, b) in enumerate(FLOWS, 1):
        conn.execute("INSERT INTO processes(code,doc_id,name,amr_role,from_location,to_location,steps,status) VALUES (?,?,?,?,?,?,'[]','confirmed')",
                     (f"PR-{i:03}", 1, f"Перемещение {PLACES[a].lower()} — {PLACES[b].lower()}", "роботизирован" if i % 3 else "вручную", PLACES[a], PLACES[b]))
    steps = json.dumps([{"actor": a, "action": t} for a, t in STEPS], ensure_ascii=False)
    conn.execute("INSERT INTO processes(code,doc_id,name,amr_role,from_location,to_location,steps,status) VALUES ('PR-100',1,'Подвоз комплектующих',"
                 "'роботизирован','Зона комплектации склада','Участок сборки 1',?,'confirmed')", (steps,))
    conn.commit()
    return tmp, conn


def lines_through_foreign_nodes(d: diagrams.Diagram) -> list[str]:
    bad = []
    for e in d.edges:
        for n in d.nodes:
            if n.id in (e.src, e.dst):
                continue
            if any(_seg_hits_rect(p, q, _rect(n)) for p, q in _segs(e.points)):
                bad.append(f"{e.src}→{e.dst} через {n.id}")
    return bad


class LayoutTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp, cls.conn = build_db()
        cls.systems = diagrams.systems_diagram(cls.conn)
        cls.logistics = diagrams.logistics_diagram(cls.conn)
        proc = db.one(cls.conn, "SELECT * FROM processes WHERE code = 'PR-100'")
        cls.process = diagrams.process_diagram(dict(proc))

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        cls.tmp.cleanup()

    # ── общие требования ──
    def check_clean(self, d: diagrams.Diagram, max_width: float):
        self.assertEqual(lines_through_foreign_nodes(d), [])
        self.assertLessEqual(d.width, max_width)
        for n in d.nodes:                                           # всё внутри холста
            self.assertTrue(0 <= n.x and n.x + n.w <= d.width and 0 <= n.y and n.y + n.h <= d.height, n.id)
        for e in d.edges:
            for x, y in e.points:
                self.assertTrue(0 <= x <= d.width and 0 <= y <= d.height, f"{e.src}→{e.dst}")
        centers = [e.label_at for e in d.edges if e.label_at]
        for c in centers:                                           # номера не лежат на блоках и друг на друге
            self.assertFalse(any(_rect(n, BADGE_R)[0] < c[0] < _rect(n, BADGE_R)[2] and _rect(n, BADGE_R)[1] < c[1] < _rect(n, BADGE_R)[3]
                                 for n in d.nodes), f"номер {c} на блоке")
        for i, a in enumerate(centers):
            for b in centers[i + 1:]:
                self.assertGreater(math.dist(a, b), 2 * BADGE_R, "номера наложились")

    def test_systems_is_readable(self):
        d = self.systems
        self.check_clean(d, 900)
        self.assertEqual(len(d.edges), 10)                          # 15 обменов, пары в обе стороны слились
        self.assertEqual(sum(e.bidir for e in d.edges), 5)
        self.assertEqual(len(d.legend), len(d.edges))
        text = " ".join(" ".join([it.head] + it.lines) for it in d.legend)
        for _a, _b, msg, _p in EDGES:                               # в легенде все сообщения и ничего не обрезано
            self.assertIn(msg, text)
        self.assertNotIn("…", text)
        self.assertEqual([it.badge for it in d.legend], [str(i) for i in range(1, 11)])

    def test_logistics_stays_narrow_despite_cycle(self):
        d = self.logistics
        self.check_clean(d, 900)                                    # раньше 3710 px
        self.assertEqual(len(d.nodes), 10)
        text = " ".join(" ".join(it.lines) for it in d.legend)
        for i in range(1, 15):
            self.assertIn(f"PR-{i:03}", text)                       # каждый процесс есть в легенде
        self.assertTrue(any("PR-100" in " ".join(it.lines) for it in d.legend))

    def test_process_is_compact_and_complete(self):
        d = self.process
        self.check_clean(d, 1000)                                   # раньше 2390 px
        self.assertEqual(len(d.nodes), 10)
        self.assertEqual(len(d.edges), 9)
        for n in d.nodes:
            self.assertNotIn("…", " ".join(n.lines), "текст шага обрезан")
            self.assertTrue(n.lines[0][:2].rstrip(".").isdigit() or n.lines[0].split(".")[0].isdigit())
        for n, (actor, _t) in zip(d.nodes, STEPS):                  # блок лежит в дорожке своего исполнителя
            lane = next(ln for ln in d.lanes if ln.label == actor)
            self.assertTrue(lane.y <= n.y and n.y + n.h <= lane.y + lane.h)
        self.assertEqual(len({(round(n.x), ln) for n, ln in zip(d.nodes, [s[0] for s in STEPS])}), 10)   # нет двух блоков в одной клетке

    # ── форматы ──
    def test_all_formats_are_valid(self):
        for d in (self.systems, self.logistics, self.process):
            ET.fromstring(diagrams.to_svg(d))
        drawio = ET.fromstring(diagrams.to_drawio([self.systems, self.logistics, self.process]))
        self.assertEqual(len(drawio.findall("diagram")), 3)
        mm = diagrams.to_mermaid(self.systems)
        self.assertIn("<-->", mm)                                   # обмен в обе стороны
        self.assertIn("flowchart", mm)

    def test_svg_has_badges_and_legend(self):
        svg = diagrams.to_svg(self.systems)
        self.assertEqual(svg.count('class="dg-badge'), 20)           # 10 на связях и 10 в легенде
        self.assertIn("marker-start", svg)

    @unittest.skipUnless(diagrams.png_available(), "нет Pillow или шрифта с кириллицей")
    def test_png_renders_with_title(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = diagrams.to_png(self.systems, Path(tmp) / "s.png", with_title=True)
            from PIL import Image
            w, h = Image.open(out).size
        self.assertEqual(w, int(self.systems.width * 2))
        self.assertGreater(h, self.systems.height * 2)             # сверху добавлен заголовок


class SmallCaseTest(unittest.TestCase):
    def test_single_one_way_exchange(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = db.connect(Path(tmp) / "s.sqlite")
            conn.execute("INSERT INTO documents(id,source_path,rel_path,sha256,ext,status,is_current) VALUES (1,'x','x','h','.docx','parsed',1)")
            conn.execute("INSERT INTO interfaces(code,doc_id,source_system,target_system,message,protocol,status) "
                         "VALUES ('IF-001',1,'SAP EWM','RMS','транспортное задание','REST','confirmed')")
            conn.commit()
            d = diagrams.systems_diagram(conn)
            conn.close()
        self.assertEqual(len(d.edges), 1)
        self.assertFalse(d.edges[0].bidir)
        self.assertNotIn("marker-start", diagrams.to_svg(d))
        self.assertEqual(lines_through_foreign_nodes(d), [])


if __name__ == "__main__":
    unittest.main()
