"""Схемы (версия 2): взаимодействие систем, общая схема логистики, карта процесса (дорожки).

Одна раскладка (узлы с координатами, связи с точками) — четыре вывода:
    to_svg     — для веб-интерфейса и как отдельный файл;
    to_drawio  — редактируемый файл diagrams.net (draw.io), все схемы — страницами одного файла;
    to_mermaid — текстовое описание, которое можно править и вставлять в Markdown/Confluence;
    to_png     — картинка для отчёта Word (нужен Pillow; без него схема идёт только файлами).
"""
from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass, field
from html import escape
from pathlib import Path

from .db import rows
from .facts import ACTIVE
from .vocab import Vocab, load_vocab

FONT = 13.0
CHAR_W = 8.2           # ширина символа при 13 px с запасом (жирный шрифт в PNG шире, чем в браузере)
LINE_H = 17.0
ROBOT_ROLES = ("роботизирован", "планируется")
LABEL_CHARS = 24       # длина строки подписи связи


# ───────── модель раскладки ─────────

@dataclass
class Node:
    id: str
    label: str
    x: float
    y: float
    w: float
    h: float
    accent: bool = False
    muted: bool = False
    sub: str | None = None          # вторая строка мелким шрифтом
    lines: list[str] = field(default_factory=list)

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2


@dataclass
class Edge:
    src: str
    dst: str
    label: str = ""
    accent: bool = False
    dashed: bool = False
    points: list[tuple[float, float]] = field(default_factory=list)
    label_at: tuple[float, float] | None = None


@dataclass
class Lane:
    label: str
    y: float
    h: float


@dataclass
class Diagram:
    key: str
    title: str
    width: float
    height: float
    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    lanes: list[Lane] = field(default_factory=list)
    lane_w: float = 0
    note: str | None = None

    @property
    def empty(self) -> bool:
        return not self.nodes

    def node(self, node_id: str) -> Node:
        return next(n for n in self.nodes if n.id == node_id)


def wrap(text: str, max_chars: int, max_lines: int = 3) -> list[str]:
    words = str(text or "").split()
    lines: list[str] = []
    cur = ""
    for w in words:
        while len(w) > max_chars:               # очень длинное слово режем
            if cur:
                lines.append(cur)
                cur = ""
            lines.append(w[:max_chars - 1] + "‑")
            w = w[max_chars - 1:]
        if not cur:
            cur = w
        elif len(cur) + 1 + len(w) <= max_chars:
            cur += " " + w
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1][: max_chars - 1].rstrip() + "…"
    return lines or [""]


def _box(node_id: str, label: str, x: float, y: float, w: float, max_lines: int = 3, min_h: float = 48, **kw) -> Node:
    lines = wrap(label, max(6, int((w - 20) / CHAR_W)), max_lines)
    h = max(min_h, 18 + LINE_H * len(lines) + (14 if kw.get("sub") else 0))
    return Node(node_id, label, x, y, w, h, lines=lines, **kw)


def _clip(n: Node, tx: float, ty: float) -> tuple[float, float]:
    """Точка на границе прямоугольника узла по направлению к (tx, ty)."""
    dx, dy = tx - n.cx, ty - n.cy
    if dx == 0 and dy == 0:
        return n.cx, n.cy
    sx = (n.w / 2) / abs(dx) if dx else math.inf
    sy = (n.h / 2) / abs(dy) if dy else math.inf
    s = min(sx, sy)
    return n.cx + dx * s, n.cy + dy * s


def _straight(d: Diagram, e: Edge, offset: float = 0.0) -> None:
    a, b = d.node(e.src), d.node(e.dst)
    dx, dy = b.cx - a.cx, b.cy - a.cy
    ln = math.hypot(dx, dy) or 1
    ox, oy = -dy / ln * offset, dx / ln * offset      # параллельный сдвиг для встречных связей
    p1 = _clip(a, b.cx + ox, b.cy + oy)
    p2 = _clip(b, a.cx + ox, a.cy + oy)
    e.points = [(p1[0] + ox, p1[1] + oy), (p2[0] + ox, p2[1] + oy)]
    if offset and e.label:
        # у встречных связей подписи разносим по разные стороны, чтобы они не накладывались
        lines = wrap(e.label, LABEL_CHARS, 2)
        half = (max(len(t) for t in lines) * 6.6 + 12) / 2 + 4 if abs(dy) >= abs(dx) else (len(lines) * 14 + 6) / 2 + 4
        mx, my = (e.points[0][0] + e.points[1][0]) / 2, (e.points[0][1] + e.points[1][1]) / 2
        sign = 1 if offset > 0 else -1
        e.label_at = (mx - dy / ln * half * sign, my + dx / ln * half * sign)


def _label_point(e: Edge) -> tuple[float, float]:
    if e.label_at:
        return e.label_at
    pts = e.points
    best = max(range(len(pts) - 1), key=lambda i: math.dist(pts[i], pts[i + 1]))
    (x1, y1), (x2, y2) = pts[best], pts[best + 1]
    return (x1 + x2) / 2, (y1 + y2) / 2


def _active(include_auto: bool) -> str:
    st = ACTIVE if include_auto else ("confirmed", "edited")
    return ",".join(f"'{s}'" for s in st)


# ───────── 1. взаимодействие систем ─────────

def systems_diagram(conn: sqlite3.Connection, include_auto: bool = True, vocab: Vocab | None = None) -> Diagram:
    v = vocab or load_vocab()
    ifs = rows(conn, f"""SELECT i.source_system, i.target_system, i.message, i.protocol FROM interfaces i
                         JOIN documents d ON d.id = i.doc_id
                         WHERE i.status IN ({_active(include_auto)}) AND d.is_current = 1 ORDER BY i.code""")
    used = {i["source_system"] for i in ifs} | {i["target_system"] for i in ifs}
    if ifs:
        used |= {"RMS", "AMR"}
    systems = [s for s in v.systems if s in used]
    d = Diagram("systems", "Взаимодействие систем в роботизированных перевозках", 960, 120)
    if not systems:
        d.note = "Обменов между системами пока нет. Запустите извлечение «Обмены и ответственность» на экране «Факты и ревью»."
        return d
    tiers: dict[int, list[str]] = {}
    for s in systems:
        tiers.setdefault(v.tiers.get(s, 1), []).append(s)
    W, BW = 960.0, 168.0
    for row, t in enumerate(sorted(tiers)):
        items = tiers[t]
        for i, s in enumerate(items):
            cx = W * (i + 1) / (len(items) + 1)
            d.nodes.append(_box(s, s, cx - BW / 2, 48 + row * 158, BW, max_lines=2, min_h=52,
                                accent=s in ("RMS", "AMR")))
    d.height = max(n.y + n.h for n in d.nodes) + 48

    pairs: dict[tuple[str, str], list[dict]] = {}
    for i in ifs:
        pairs.setdefault((i["source_system"], i["target_system"]), []).append(i)
    for (a, b), lst in pairs.items():
        msgs = list(dict.fromkeys(i["message"] for i in lst))
        protos = sorted({i["protocol"] for i in lst if i["protocol"]})
        label = "; ".join(msgs[:2]) + (f" +{len(msgs) - 2}" if len(msgs) > 2 else "")
        if protos:
            label += f" ({', '.join(protos)})"
        e = Edge(a, b, label, accent="RMS" in (a, b) and ("AMR" in (a, b) or "SAP EWM" in (a, b) or "MES" in (a, b)))
        _straight(d, e, offset=11 if (b, a) in pairs else 0)
        d.edges.append(e)
    return d


# ───────── 2. общая схема логистики ─────────

def _loc_key(s: str) -> str:
    """«Участки сборки» и «Участок сборки» — одно место на схеме."""
    from .routes import PLACES
    k = " ".join(str(s).lower().replace("ё", "е").split())
    for sing, plur in PLACES.values():
        pl = plur.lower().replace("ё", "е")
        if pl != sing.lower() and (k == pl or k.startswith(pl + " ")):
            return sing.lower() + k[len(pl):]
    return k


def _loc_label(s: str) -> str:
    s = " ".join(str(s).split())
    return s[:1].upper() + s[1:]


def logistics_diagram(conn: sqlite3.Connection, include_auto: bool = True) -> Diagram:
    procs = rows(conn, f"""SELECT p.code, p.name, p.from_location, p.to_location, p.cargo, p.amr_role FROM processes p
                           JOIN documents d ON d.id = p.doc_id
                           WHERE p.status IN ({_active(include_auto)}) AND d.is_current = 1 ORDER BY p.code""")
    d = Diagram("logistics", "Материальные потоки и участки роботизации", 960, 120)
    flows = [p for p in procs if (p["from_location"] or "").strip() and (p["to_location"] or "").strip()]
    skipped = len(procs) - len(flows)
    if not flows:
        d.note = (f"У процессов ({skipped}) не указано, откуда и куда перемещается груз. Откройте процесс на вкладке "
                  "«Процессы с AMR» и заполните поля «Откуда» и «Куда» — он появится на схеме."
                  if skipped else "Процессов пока нет. Запустите извлечение «Процессы» на экране «Факты и ревью».")
        return d
    labels: dict[str, str] = {}
    out_e: dict[str, set[str]] = {}
    for p in flows:
        a, b = _loc_key(p["from_location"]), _loc_key(p["to_location"])
        labels.setdefault(a, _loc_label(p["from_location"]))
        labels.setdefault(b, _loc_label(p["to_location"]))
        if a != b:
            out_e.setdefault(a, set()).add(b)
    # слои: самый длинный путь от источников (циклы обрываются)
    layer: dict[str, int] = {k: 0 for k in labels}
    for _ in range(len(labels)):
        changed = False
        for a, bs in out_e.items():
            for b in bs:
                if layer[b] < layer[a] + 1 and layer[a] + 1 < len(labels):
                    layer[b] = layer[a] + 1
                    changed = True
        if not changed:
            break
    cols: dict[int, list[str]] = {}
    for k in labels:                                    # порядок появления сохраняется
        cols.setdefault(layer[k], []).append(k)
    BW, GX, GY = 210.0, 380.0, 130.0
    for c, keys in cols.items():
        for r, k in enumerate(keys):
            d.nodes.append(_box(k, labels[k], 40 + c * GX, 56 + r * GY, BW, max_lines=3))
    d.width = max(n.x + n.w for n in d.nodes) + 40
    d.height = max(n.y + n.h for n in d.nodes) + 56

    merged: dict[tuple[str, str], list[dict]] = {}
    for p in flows:
        a, b = _loc_key(p["from_location"]), _loc_key(p["to_location"])
        if a != b:
            merged.setdefault((a, b), []).append(p)
    robot_nodes: set[str] = set()
    for (a, b), lst in merged.items():
        robot = any(p["amr_role"] in ROBOT_ROLES for p in lst)
        talk = all(p["amr_role"] == "обсуждается" for p in lst)
        names = list(dict.fromkeys(p["name"] for p in lst))
        label = names[0] + (f" +{len(names) - 1}" if len(names) > 1 else "")
        if robot:
            label = "AMR · " + label
            robot_nodes |= {a, b}
        e = Edge(a, b, label, accent=robot, dashed=talk)
        _straight(d, e, offset=11 if (b, a) in merged else 0)
        d.edges.append(e)
    for n in d.nodes:
        n.accent = n.id in robot_nodes
    if skipped:
        d.note = (f"Процессов без «откуда» и «куда»: {skipped}. Они есть на вкладке «Процессы с AMR», "
                  "но на схему попадут, когда места будут заполнены.")
    return d


# ───────── 3. карта процесса (дорожки) ─────────

def process_steps(p: dict) -> list[dict]:
    try:
        steps = json.loads(p.get("steps") or "[]")
    except (TypeError, ValueError):
        steps = []
    return [s for s in steps if isinstance(s, dict) and s.get("action")]


def process_diagram(p: dict) -> Diagram:
    steps = process_steps(p)
    route = " → ".join(x for x in (p.get("from_location"), p.get("to_location")) if x)
    d = Diagram(f"process-{p.get('code')}", f"{p.get('code')} · {p.get('name')}", 960, 160)
    if route:
        d.note = route + (f" · {p['cargo']}" if p.get("cargo") else "")
    if not steps:
        d.nodes.append(_box("only", p.get("name") or "Процесс", 40, 40, 260, accent=p.get("amr_role") in ROBOT_ROLES))
        d.height = d.nodes[0].h + 80
        return d
    actors = list(dict.fromkeys(s.get("actor") or "Другое" for s in steps))
    LW, CW, BW, LH = 150.0, 220.0, 196.0, 112.0
    d.lane_w = LW
    d.lanes = [Lane(a, 24 + i * LH, LH) for i, a in enumerate(actors)]
    for i, s in enumerate(steps):
        lane = d.lanes[actors.index(s.get("actor") or "Другое")]
        n = _box(f"s{i}", s["action"], LW + 16 + i * CW, 0, BW, max_lines=4, min_h=52,
                 accent=(s.get("actor") == "AMR"))
        n.h = min(n.h, LH - 16)
        n.y = lane.y + (lane.h - n.h) / 2
        d.nodes.append(n)
    d.width = LW + 16 + len(steps) * CW + 24
    d.height = d.lanes[-1].y + d.lanes[-1].h + 24
    for i in range(len(steps) - 1):
        a, b = d.nodes[i], d.nodes[i + 1]
        x1, y1 = a.x + a.w, a.cy
        x2, y2 = b.x, b.cy
        if abs(y1 - y2) < 1:
            pts = [(x1, y1), (x2, y2)]
        else:
            xm = (x1 + x2) / 2
            pts = [(x1, y1), (xm, y1), (xm, y2), (x2, y2)]
        d.edges.append(Edge(a.id, b.id, "", accent=False, points=pts))
    return d


# ───────── вывод: SVG ─────────

SVG_STYLE = """
.dg{font-family:Manrope,'Segoe UI',Arial,sans-serif}
.dg-title{fill:#15122a;font-size:15px;font-weight:700}
.dg-note{fill:#5b5873;font-size:12px}
.dg-node rect{fill:#ffffff;stroke:#c3c0d3;stroke-width:1.2}
.dg-node--accent rect{fill:#efe9fd;stroke:#6d3ce8;stroke-width:1.8}
.dg-node text{fill:#15122a;font-size:13px;font-weight:600}
.dg-node .dg-sub{fill:#5b5873;font-size:11px;font-weight:500}
.dg-edge{stroke:#8b88a0;fill:none;stroke-width:1.3}
.dg-edge--accent{stroke:#6d3ce8;stroke-width:2}
.dg-edge--dashed{stroke-dasharray:6 4}
.dg-arrow{fill:#8b88a0}
.dg-arrow--accent{fill:#6d3ce8}
.dg-elabel rect{fill:#ffffff;fill-opacity:.94}
.dg-elabel text{fill:#5b5873;font-size:11.5px;font-weight:600}
.dg-lane rect{fill:#f6f5f9;stroke:#e3e1ec}
.dg-lane text{fill:#5b5873;font-size:12px;font-weight:700}
"""


def _svg_text_lines(x: float, y_center: float, lines: list[str], cls: str = "", anchor: str = "middle",
                    line_h: float = LINE_H) -> str:
    top = y_center - (len(lines) - 1) * line_h / 2 + FONT / 3
    return "".join(f'<text x="{x:.1f}" y="{top + i * line_h:.1f}" text-anchor="{anchor}"{f" class={chr(34)}{cls}{chr(34)}" if cls else ""}>'
                   f"{escape(t)}</text>" for i, t in enumerate(lines))


def to_svg(d: Diagram, uid: str = "dg", with_title: bool = False) -> str:
    head = 36 if with_title else 0
    W, H = d.width, d.height + head
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" class="dg" viewBox="0 0 {W:.0f} {H:.0f}" '
           f'width="{W:.0f}" height="{H:.0f}" role="img" aria-label="{escape(d.title)}">',
           f"<style>{SVG_STYLE}</style>",
           "<defs>"]
    for suffix, cls in (("", "dg-arrow"), ("-a", "dg-arrow dg-arrow--accent")):
        out.append(f'<marker id="{uid}-arr{suffix}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
                   f'orient="auto-start-reverse"><path class="{cls}" d="M0 0L10 5L0 10z"/></marker>')
    out.append("</defs>")
    if with_title:
        out.append(f'<text class="dg-title" x="16" y="24">{escape(d.title)}</text>')
    out.append(f'<g transform="translate(0 {head})">')
    for ln in d.lanes:
        out.append(f'<g class="dg-lane"><rect x="8" y="{ln.y:.1f}" width="{W - 16:.1f}" height="{ln.h - 6:.1f}" rx="10"/>'
                   + _svg_text_lines(20, ln.y + (ln.h - 6) / 2, wrap(ln.label, 16, 2), anchor="start") + "</g>")
    for e in d.edges:
        cls = "dg-edge" + (" dg-edge--accent" if e.accent else "") + (" dg-edge--dashed" if e.dashed else "")
        pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in e.points)
        out.append(f'<polyline class="{cls}" points="{pts}" marker-end="url(#{uid}-arr{"-a" if e.accent else ""})"/>')
    for n in d.nodes:
        cls = "dg-node" + (" dg-node--accent" if n.accent else "")
        body = _svg_text_lines(n.cx, n.cy - (7 if n.sub else 0), n.lines)
        if n.sub:
            body += f'<text class="dg-sub" x="{n.cx:.1f}" y="{n.y + n.h - 10:.1f}" text-anchor="middle">{escape(n.sub)}</text>'
        out.append(f'<g class="{cls}" data-id="{escape(n.id)}"><rect x="{n.x:.1f}" y="{n.y:.1f}" width="{n.w:.1f}" '
                   f'height="{n.h:.1f}" rx="10"/>{body}</g>')
    for e in d.edges:
        if not e.label:
            continue
        lines = wrap(e.label, LABEL_CHARS, 2)
        lx, ly = _label_point(e)
        w = max(len(t) for t in lines) * 6.6 + 12
        h = len(lines) * 14 + 6
        out.append(f'<g class="dg-elabel"><rect x="{lx - w / 2:.1f}" y="{ly - h / 2:.1f}" width="{w:.1f}" height="{h:.1f}" rx="6"/>'
                   + _svg_text_lines(lx, ly, lines, line_h=14) + "</g>")
    out.append("</g></svg>")
    return "".join(out)


# ───────── вывод: draw.io ─────────

def _drawio_page(d: Diagram, page_id: str) -> str:
    cells = ['<mxCell id="0"/>', '<mxCell id="1" parent="0"/>']
    for i, ln in enumerate(d.lanes):
        cells.append(f'<mxCell id="{page_id}-lane{i}" value="{escape(ln.label)}" '
                     f'style="rounded=1;arcSize=6;whiteSpace=wrap;html=1;fillColor=#f6f5f9;strokeColor=#e3e1ec;'
                     f'align=left;verticalAlign=middle;spacingLeft=12;fontStyle=1;fontColor=#5b5873;" vertex="1" parent="1">'
                     f'<mxGeometry x="8" y="{ln.y:.0f}" width="{d.width - 16:.0f}" height="{ln.h - 6:.0f}" as="geometry"/></mxCell>')
    ids = {}
    for i, n in enumerate(d.nodes):
        nid = f"{page_id}-n{i}"
        ids[n.id] = nid
        fill, stroke = ("#efe9fd", "#6d3ce8") if n.accent else ("#ffffff", "#c3c0d3")
        cells.append(f'<mxCell id="{nid}" value="{escape(n.label)}" style="rounded=1;whiteSpace=wrap;html=1;fillColor={fill};'
                     f'strokeColor={stroke};fontStyle=1;fontSize=12;" vertex="1" parent="1">'
                     f'<mxGeometry x="{n.x:.0f}" y="{n.y:.0f}" width="{n.w:.0f}" height="{n.h:.0f}" as="geometry"/></mxCell>')
    for i, e in enumerate(d.edges):
        color = "#6d3ce8" if e.accent else "#8b88a0"
        style = (f"endArrow=block;endFill=1;html=1;rounded=0;strokeColor={color};strokeWidth={2 if e.accent else 1};"
                 f"fontSize=11;labelBackgroundColor=#ffffff;" + ("dashed=1;" if e.dashed else ""))
        inner = ""
        if len(e.points) > 2:
            inner = "<Array as=\"points\">" + "".join(f'<mxPoint x="{x:.0f}" y="{y:.0f}"/>' for x, y in e.points[1:-1]) + "</Array>"
        cells.append(f'<mxCell id="{page_id}-e{i}" value="{escape(e.label)}" style="{style}" edge="1" parent="1" '
                     f'source="{ids[e.src]}" target="{ids[e.dst]}"><mxGeometry relative="1" as="geometry">{inner}</mxGeometry></mxCell>')
    return (f'<diagram id="{page_id}" name="{escape(d.title[:60])}"><mxGraphModel grid="1" gridSize="10" page="1" '
            f'pageWidth="{d.width:.0f}" pageHeight="{d.height:.0f}"><root>{"".join(cells)}</root></mxGraphModel></diagram>')


def to_drawio(diagrams: list[Diagram]) -> str:
    pages = "".join(_drawio_page(d, f"p{i}") for i, d in enumerate(diagrams) if not d.empty)
    return f'<mxfile host="DocAnalyzer AMR/RMS" type="device">{pages}</mxfile>'


# ───────── вывод: Mermaid ─────────

def _mm(s: str) -> str:
    return str(s).replace('"', "'").replace("\n", " ")


def to_mermaid(d: Diagram) -> str:
    ids = {n.id: f"N{i}" for i, n in enumerate(d.nodes)}
    lines = [f"%% {d.title}", "flowchart LR" if d.lanes or d.key == "logistics" else "flowchart TB"]
    if d.lanes:
        for li, ln in enumerate(d.lanes):
            lines.append(f'  subgraph L{li}["{_mm(ln.label)}"]')
            for n in d.nodes:
                if ln.y <= n.cy <= ln.y + ln.h:
                    lines.append(f'    {ids[n.id]}["{_mm(n.label)}"]')
            lines.append("  end")
    else:
        for n in d.nodes:
            lines.append(f'  {ids[n.id]}["{_mm(n.label)}"]')
    for e in d.edges:
        arrow = "-.->" if e.dashed else ("==>" if e.accent else "-->")
        lab = f'|"{_mm(e.label)}"|' if e.label else ""
        lines.append(f"  {ids[e.src]} {arrow}{lab} {ids[e.dst]}")
    accents = [ids[n.id] for n in d.nodes if n.accent]
    if accents:
        lines.append("  classDef amr fill:#efe9fd,stroke:#6d3ce8,stroke-width:2px")
        lines.append(f"  class {','.join(accents)} amr")
    return "\n".join(lines) + "\n"


# ───────── вывод: PNG (для Word) ─────────

FONT_CANDIDATES = [
    "C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
]
FONT_BOLD_CANDIDATES = [
    "C:/Windows/Fonts/segoeuib.ttf", "C:/Windows/Fonts/arialbd.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
]


def _font(candidates: list[str], size: int):
    from PIL import ImageFont
    for p in candidates:
        if Path(p).exists():
            return ImageFont.truetype(p, size)
    return None


def png_available() -> bool:
    try:
        import PIL  # noqa: F401
    except ImportError:
        return False
    return _font(FONT_CANDIDATES, 12) is not None


def to_png(d: Diagram, path: Path, scale: float = 2.0) -> Path | None:
    """Рисует схему в PNG. Возвращает путь или None, если нет Pillow или шрифта с кириллицей."""
    if not png_available() or d.empty:
        return None
    from PIL import Image, ImageDraw

    s = scale
    f_node = _font(FONT_BOLD_CANDIDATES, int(12 * s)) or _font(FONT_CANDIDATES, int(12 * s))
    f_small = _font(FONT_CANDIDATES, int(11 * s))
    img = Image.new("RGB", (int(d.width * s), int(d.height * s)), "white")
    dr = ImageDraw.Draw(img)
    P = lambda x, y: (x * s, y * s)  # noqa: E731

    def text_center(cx, cy, lines, font, fill, lh):
        top = cy - (len(lines) - 1) * lh / 2
        for i, t in enumerate(lines):
            dr.text(P(cx, top + i * lh), t, font=font, fill=fill, anchor="mm")

    for ln in d.lanes:
        dr.rounded_rectangle([P(8, ln.y), P(d.width - 8, ln.y + ln.h - 6)], radius=10 * s, fill="#f6f5f9", outline="#e3e1ec")
        lines = wrap(ln.label, 16, 2)
        top = ln.y + (ln.h - 6) / 2 - (len(lines) - 1) * 8
        for i, t in enumerate(lines):
            dr.text(P(20, top + i * 16), t, font=f_small, fill="#5b5873", anchor="lm")
    for e in d.edges:
        color = "#6d3ce8" if e.accent else "#8b88a0"
        width = int((2 if e.accent else 1.3) * s)
        pts = [P(x, y) for x, y in e.points]
        if e.dashed:
            for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
                ln_ = math.dist((x1, y1), (x2, y2))
                n = max(1, int(ln_ / (10 * s)))
                for k in range(n):
                    if k % 2 == 0:
                        t1, t2 = k / n, min(1, (k + 1) / n)
                        dr.line([(x1 + (x2 - x1) * t1, y1 + (y2 - y1) * t1), (x1 + (x2 - x1) * t2, y1 + (y2 - y1) * t2)],
                                fill=color, width=width)
        else:
            dr.line(pts, fill=color, width=width, joint="curve")
        (x1, y1), (x2, y2) = pts[-2], pts[-1]
        ang = math.atan2(y2 - y1, x2 - x1)
        a = 9 * s
        dr.polygon([(x2, y2), (x2 - a * math.cos(ang - 0.42), y2 - a * math.sin(ang - 0.42)),
                    (x2 - a * math.cos(ang + 0.42), y2 - a * math.sin(ang + 0.42))], fill=color)
    for n in d.nodes:
        fill, outline = ("#efe9fd", "#6d3ce8") if n.accent else ("#ffffff", "#c3c0d3")
        dr.rounded_rectangle([P(n.x, n.y), P(n.x + n.w, n.y + n.h)], radius=10 * s, fill=fill, outline=outline,
                             width=int((1.8 if n.accent else 1.2) * s))
        text_center(n.cx, n.cy, n.lines, f_node, "#15122a", LINE_H)
    for e in d.edges:
        if not e.label:
            continue
        lines = wrap(e.label, LABEL_CHARS, 2)
        lx, ly = _label_point(e)
        w = max(dr.textlength(t, font=f_small) for t in lines) / s + 12
        h = len(lines) * 14 + 6
        dr.rounded_rectangle([P(lx - w / 2, ly - h / 2), P(lx + w / 2, ly + h / 2)], radius=6 * s, fill="white")
        text_center(lx, ly, lines, f_small, "#5b5873", 14)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "PNG", optimize=True)
    return path


# ───────── все схемы проекта ─────────

def process_rows(conn: sqlite3.Connection, include_auto: bool = True) -> list[dict]:
    return rows(conn, f"""SELECT p.*, d.rel_path FROM processes p JOIN documents d ON d.id = p.doc_id
                          WHERE p.status IN ({_active(include_auto)}) AND d.is_current = 1 ORDER BY p.code""")


def all_diagrams(conn: sqlite3.Connection, include_auto: bool = True) -> list[Diagram]:
    out = [systems_diagram(conn, include_auto), logistics_diagram(conn, include_auto)]
    out += [process_diagram(p) for p in process_rows(conn, include_auto) if p["amr_role"] in ROBOT_ROLES + ("обсуждается",)]
    return out
