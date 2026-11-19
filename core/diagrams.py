"""Схемы (версия 2): взаимодействие систем, общая схема логистики, карта процесса (дорожки).

Одна раскладка (узлы с координатами, связи с точками) — четыре вывода:
    to_svg     — для веб-интерфейса и как отдельный файл;
    to_drawio  — редактируемый файл diagrams.net (draw.io), все схемы — страницами одного файла;
    to_mermaid — текстовое описание, которое можно править и вставлять в Markdown/Confluence;
    to_png     — картинка для отчёта Word (нужен Pillow; без него схема идёт только файлами).

Читаемость (версия 2.3). Подписи на самих связях накладываются друг на друга и на блоки, поэтому на связи
ставится номер, а полный текст идёт в легенду под схемой. Линии прокладываются в обход блоков,
возвратные потоки — дугами. Раскладка держит ширину в пределах страницы Word.
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
LABEL_CHARS = 24       # длина строки подписи связи (для связей без номера)
BADGE_R = 11.0         # радиус номера на связи
LEG_LINE = 15.0        # высота строки легенды
LEG_CHAR_W = 6.4       # ширина символа легенды при 11.5 px
MARGIN = 36.0
MIN_WIDTH = 600.0


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
    label: str = ""                 # полный текст связи (Mermaid, доступность)
    accent: bool = False
    dashed: bool = False
    bidir: bool = False             # стрелки на обоих концах: обмен в обе стороны
    badge: str = ""                 # номер на связи; расшифровка — в легенде
    points: list[tuple[float, float]] = field(default_factory=list)
    label_at: tuple[float, float] | None = None     # центр номера


@dataclass
class Lane:
    label: str
    y: float
    h: float


@dataclass
class LegendItem:
    badge: str
    head: str
    lines: list[str]
    accent: bool = False
    x: float = 0.0
    y: float = 0.0


@dataclass
class Diagram:
    key: str
    title: str
    width: float
    height: float
    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    lanes: list[Lane] = field(default_factory=list)
    legend: list[LegendItem] = field(default_factory=list)
    lane_w: float = 0
    note: str | None = None
    graph_height: float = 0          # высота схемы без легенды (0 — легенды нет)

    @property
    def plot_height(self) -> float:
        return self.graph_height or self.height

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


# ───────── геометрия и прокладка связей ─────────

Pt = tuple[float, float]


def _segs(pts: list[Pt]):
    return list(zip(pts, pts[1:]))


def _seg_hits_rect(p: Pt, q: Pt, r: tuple[float, float, float, float]) -> bool:
    """Проходит ли отрезок через внутренность прямоугольника (Лианга — Барски)."""
    x1, y1 = p
    dx, dy = q[0] - x1, q[1] - y1
    t0, t1 = 0.0, 1.0
    for pp, qq in ((-dx, x1 - r[0]), (dx, r[2] - x1), (-dy, y1 - r[1]), (dy, r[3] - y1)):
        if pp == 0:
            if qq <= 0:
                return False
        else:
            t = qq / pp
            if pp < 0:
                if t > t1:
                    return False
                t0 = max(t0, t)
            else:
                if t < t0:
                    return False
                t1 = min(t1, t)
    return t1 - t0 > 1e-6


def _ccw(a: Pt, b: Pt, c: Pt) -> float:
    return (c[1] - a[1]) * (b[0] - a[0]) - (b[1] - a[1]) * (c[0] - a[0])


def _cross(p1: Pt, p2: Pt, p3: Pt, p4: Pt) -> bool:
    return _ccw(p3, p4, p1) * _ccw(p3, p4, p2) < 0 and _ccw(p1, p2, p3) * _ccw(p1, p2, p4) < 0


def _overlap(p1: Pt, p2: Pt, p3: Pt, p4: Pt, tol: float = 4.0) -> bool:
    """Два горизонтальных или два вертикальных отрезка идут друг по другу."""
    if abs(p1[1] - p2[1]) < 1 and abs(p3[1] - p4[1]) < 1 and abs(p1[1] - p3[1]) < tol:
        return min(max(p1[0], p2[0]), max(p3[0], p4[0])) - max(min(p1[0], p2[0]), min(p3[0], p4[0])) > 6
    if abs(p1[0] - p2[0]) < 1 and abs(p3[0] - p4[0]) < 1 and abs(p1[0] - p3[0]) < tol:
        return min(max(p1[1], p2[1]), max(p3[1], p4[1])) - max(min(p1[1], p2[1]), min(p3[1], p4[1])) > 6
    return False


def _rect(n: Node, pad: float = 0.0) -> tuple[float, float, float, float]:
    return n.x - pad, n.y - pad, n.x + n.w + pad, n.y + n.h + pad


def _line(a: Node, b: Node) -> list[Pt]:
    return [_clip(a, b.cx, b.cy), _clip(b, a.cx, a.cy)]


def _dedupe(pts: list[Pt]) -> list[Pt]:
    out = [pts[0]]
    for p in pts[1:]:
        if math.dist(p, out[-1]) > 0.5:
            out.append(p)
    return out


def _port(n: Node, off: float, horizontal: bool) -> float:
    """Смещение точки выхода вдоль стороны блока, не ближе 14 px к углу."""
    if horizontal:
        return min(max(n.cx + off, n.x + 14), n.x + n.w - 14)
    return min(max(n.cy + off, n.y + 14), n.y + n.h - 14)


def _candidates(a: Node, b: Node, box: tuple[float, float, float, float]) -> list[list[Pt]]:
    """Варианты маршрута связи: прямая, ломаные с колена в промежутке и дуги вокруг блоков."""
    out: list[list[Pt]] = [_line(a, b)]
    gap = 10.0
    offs = (0, -26, 26)
    if a.y + a.h + gap <= b.y or b.y + b.h + gap <= a.y:                   # блоки в разных рядах
        up = a.y + a.h + gap <= b.y
        ea, eb = (a.y + a.h, b.y) if up else (a.y, b.y + b.h)
        for shift in (0, -22, 22):
            ym = (ea + eb) / 2 + shift
            for oa in offs:
                for ob in offs:
                    ax, bx = _port(a, oa, True), _port(b, ob, True)
                    out.append(_dedupe([(ax, ea), (ax, ym), (bx, ym), (bx, eb)]))
    if a.x + a.w + gap <= b.x or b.x + b.w + gap <= a.x:                   # блоки в разных колонках
        rt = a.x + a.w + gap <= b.x
        ea, eb = (a.x + a.w, b.x) if rt else (a.x, b.x + b.w)
        for shift in (0, -22, 22):
            xm = (ea + eb) / 2 + shift
            for oa in offs:
                for ob in offs:
                    ay, by = _port(a, oa, False), _port(b, ob, False)
                    out.append(_dedupe([(ea, ay), (xm, ay), (xm, by), (eb, by)]))
    for k in range(5):                                                       # дуги вокруг пары блоков
        d = 18 + 13 * k
        ax, bx = a.cx + (k - 2) * 7, b.cx + (k - 2) * 7
        ay, by = a.cy + (k - 2) * 5, b.cy + (k - 2) * 5
        out.append(_dedupe([(ax, a.y), (ax, min(a.y, b.y) - d), (bx, min(a.y, b.y) - d), (bx, b.y)]))
        out.append(_dedupe([(ax, a.y + a.h), (ax, max(a.y + a.h, b.y + b.h) + d), (bx, max(a.y + a.h, b.y + b.h) + d), (bx, b.y + b.h)]))
        out.append(_dedupe([(a.x, ay), (min(a.x, b.x) - d, ay), (min(a.x, b.x) - d, by), (b.x, by)]))
        out.append(_dedupe([(a.x + a.w, ay), (max(a.x + a.w, b.x + b.w) + d, ay), (max(a.x + a.w, b.x + b.w) + d, by), (b.x + b.w, by)]))
    x0, y0, x1, y1 = box                                                     # дуги за пределами всей схемы
    for k in range(4):
        d = 22 + 14 * k
        ax, bx = a.cx + (k - 1.5) * 8, b.cx + (k - 1.5) * 8
        ay, by = a.cy + (k - 1.5) * 6, b.cy + (k - 1.5) * 6
        out.append(_dedupe([(ax, a.y + a.h), (ax, y1 + d), (bx, y1 + d), (bx, b.y + b.h)]))
        out.append(_dedupe([(ax, a.y), (ax, y0 - d), (bx, y0 - d), (bx, b.y)]))
        out.append(_dedupe([(a.x + a.w, ay), (x1 + d, ay), (x1 + d, by), (b.x + b.w, by)]))
        out.append(_dedupe([(a.x, ay), (x0 - d, ay), (x0 - d, by), (b.x, by)]))
    return [c for c in out if len(c) >= 2]


def _cost(pts: list[Pt], a: Node, b: Node, nodes: list[Node], placed: list[list[Pt]]) -> float:
    c = 0.0
    segs = _segs(pts)
    for n in nodes:
        r = _rect(n, 5)
        if n is a or n is b:
            # своя граница допустима: проверяем только «середину» первого и последнего отрезков
            continue
        c += 1000 * sum(_seg_hits_rect(p, q, r) for p, q in segs)
    for own in (a, b):                       # ломаная не должна заходить внутрь своих блоков
        r = _rect(own, -2)
        c += 800 * sum(_seg_hits_rect(p, q, r) for p, q in segs)
    for other in placed:
        for p, q in segs:
            for p2, q2 in _segs(other):
                if _cross(p, q, p2, q2):
                    c += 40
                elif _overlap(p, q, p2, q2):
                    c += 70
    c += sum(math.dist(p, q) for p, q in segs) * 0.05 + (len(pts) - 2) * 8
    return c


def _route_edges(d: Diagram, edges: list[Edge]) -> None:
    """Прокладывает связи по очереди: самые «трудные» (далёкие, в одном ряду) первыми."""
    if not d.nodes:
        return
    box = (min(n.x for n in d.nodes), min(n.y for n in d.nodes),
           max(n.x + n.w for n in d.nodes), max(n.y + n.h for n in d.nodes))
    order = sorted(edges, key=lambda e: -math.dist((d.node(e.src).cx, d.node(e.src).cy), (d.node(e.dst).cx, d.node(e.dst).cy)))
    placed: list[list[Pt]] = []
    for e in order:
        a, b = d.node(e.src), d.node(e.dst)
        best = min(_candidates(a, b, box), key=lambda pts: _cost(pts, a, b, d.nodes, placed))
        e.points = best
        placed.append(best)


def _place_badges(d: Diagram, r: float = BADGE_R) -> None:
    """Номер ставится на самый длинный участок связи, где он не лежит на блоках и других номерах."""
    taken: list[Pt] = []
    rects = [_rect(n, r + 2) for n in d.nodes]
    for e in d.edges:
        if not e.badge:
            continue
        cands: list[Pt] = []
        for p, q in sorted(_segs(e.points), key=lambda s: -math.dist(*s)):
            ln = math.dist(p, q)
            if ln < 2 * r + 24:
                continue
            for t in (0.5, 0.4, 0.6, 0.3, 0.7, 0.24, 0.76):
                cands.append((p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t))
        if not cands:
            p, q = max(_segs(e.points), key=lambda s: math.dist(*s))
            cands = [((p[0] + q[0]) / 2, (p[1] + q[1]) / 2)]
        free = [c for c in cands
                if not any(x0 < c[0] < x1 and y0 < c[1] < y1 for x0, y0, x1, y1 in rects)
                and all(math.dist(c, t) > 2 * r + 4 for t in taken)]
        e.label_at = (free or cands)[0]
        taken.append(e.label_at)


def _fit(d: Diagram, margin: float = MARGIN, min_width: float = MIN_WIDTH) -> None:
    """Сдвигает схему так, чтобы всё (включая дуги и номера) попало в поле, и задаёт размеры."""
    xs: list[float] = []
    ys: list[float] = []
    for n in d.nodes:
        xs += [n.x, n.x + n.w]
        ys += [n.y, n.y + n.h]
    for e in d.edges:
        for x, y in e.points:
            xs.append(x)
            ys.append(y)
        if e.label_at:
            xs += [e.label_at[0] - BADGE_R, e.label_at[0] + BADGE_R]
            ys += [e.label_at[1] - BADGE_R, e.label_at[1] + BADGE_R]
    width = max(xs) - min(xs) + 2 * margin
    dx = margin - min(xs) + max(0.0, (min_width - width) / 2)
    dy = margin - min(ys)
    for n in d.nodes:
        n.x += dx
        n.y += dy
    for e in d.edges:
        e.points = [(x + dx, y + dy) for x, y in e.points]
        if e.label_at:
            e.label_at = (e.label_at[0] + dx, e.label_at[1] + dy)
    d.width = max(width, min_width)
    d.height = max(ys) - min(ys) + 2 * margin


def _legend(d: Diagram, items: list[tuple[str, str, list[str], bool]]) -> None:
    """Легенда под схемой: номер, заголовок и полный текст связи. Две колонки, если схема достаточно широкая."""
    if not items:
        return
    d.graph_height = d.height
    cols = 2 if d.width >= 700 and len(items) > 3 else 1  # ширину схем со связями _finish доводит до 760
    col_w = (d.width - 2 * MARGIN - (cols - 1) * 28) / cols
    chars = max(20, int((col_w - 30) / LEG_CHAR_W))
    blocks = []
    for badge, head, body, accent in items:
        lines: list[str] = []
        for para in body:
            lines += wrap(para, chars, 5)
        blocks.append((badge, wrap(head, chars, 1)[0], lines, accent, (1 + len(lines)) * LEG_LINE + 10))
    total = sum(b[4] for b in blocks)
    per_col: list[list] = [[] for _ in range(cols)]
    acc, c = 0.0, 0
    for blk in blocks:                                     # колонки примерно равной высоты, порядок номеров сохраняется
        if c < cols - 1 and acc + blk[4] / 2 > total / cols:
            c, acc = c + 1, 0.0
        per_col[c].append(blk)
        acc += blk[4]
    top = d.height - MARGIN + 30
    heights = []
    for ci, col in enumerate(per_col):
        y = top
        for badge, head, lines, accent, h in col:
            d.legend.append(LegendItem(badge, head, lines, accent, MARGIN + ci * (col_w + 28), y))
            y += h
        heights.append(y - top)
    d.height = top + max(heights, default=0) + MARGIN - 10


def _number_edges(d: Diagram) -> None:
    """Номера по порядку чтения: сверху вниз, слева направо."""
    def key(e: Edge):
        a, b = d.node(e.src), d.node(e.dst)
        top = min(a, b, key=lambda n: (n.cy, n.cx))
        return (round(top.cy / 40), top.cx, max(a.cx, b.cx))
    for i, e in enumerate(sorted(d.edges, key=key), start=1):
        e.badge = str(i)


def _finish(d: Diagram, legend_items: dict[int, tuple[str, list[str]]]) -> None:
    """Номера, положение номеров, размеры и легенда. legend_items: id(Edge) -> (заголовок, строки)."""
    _number_edges(d)
    _place_badges(d)
    _fit(d, min_width=MIN_WIDTH + 160 if len(d.edges) > 3 else MIN_WIDTH)
    items = [(e.badge, *legend_items[id(e)], e.accent) for e in sorted(d.edges, key=lambda e: int(e.badge))]
    _legend(d, items)


def _active(include_auto: bool) -> str:
    st = ACTIVE if include_auto else ("confirmed", "edited")
    return ",".join(f"'{s}'" for s in st)


# ───────── общие приёмы раскладки ─────────

def _barycenter_order(layers: dict[int, list[str]], neighbours: dict[str, set[str]], sweeps: int = 4) -> None:
    """Порядок узлов внутри слоя: связанные узлы ставятся друг над другом, чтобы линии реже пересекались."""
    keys = sorted(layers)
    for it in range(sweeps):
        seq = keys if it % 2 == 0 else keys[::-1]
        for k in seq:
            ref = k - 1 if it % 2 == 0 else k + 1
            pos = {n: (i + 0.5) / max(len(layers[ref]), 1) for i, n in enumerate(layers.get(ref, []))}
            row = layers[k]
            base = {n: (i + 0.5) / len(row) for i, n in enumerate(row)}
            def bary(n: str) -> float:
                ps = [pos[m] for m in neighbours.get(n, ()) if m in pos]
                return sum(ps) / len(ps) if ps else base[n]
            layers[k] = sorted(row, key=lambda n: (bary(n), base[n]))


def _msg(i: dict) -> str:
    t = i["message"] + (f" ({i['protocol']})" if i.get("protocol") else "")
    return t + (f" [{i['code']}]" if i.get("code") else "")


# ───────── 1. взаимодействие систем ─────────

def systems_diagram(conn: sqlite3.Connection, include_auto: bool = True, vocab: Vocab | None = None) -> Diagram:
    v = vocab or load_vocab()
    ifs = rows(conn, f"""SELECT i.code, i.source_system, i.target_system, i.message, i.protocol FROM interfaces i
                         JOIN documents d ON d.id = i.doc_id
                         WHERE i.status IN ({_active(include_auto)}) AND d.is_current = 1 ORDER BY i.code""")
    used = {i["source_system"] for i in ifs} | {i["target_system"] for i in ifs}
    if ifs:
        used |= {"RMS", "AMR"}
    systems = [s for s in v.systems if s in used]
    d = Diagram("systems", "Взаимодействие систем в роботизированных перевозках", 760, 120)
    if not systems:
        d.note = "Обменов между системами пока нет. Запустите извлечение «Обмены и ответственность» на экране «Факты и ревью»."
        return d

    # пары систем: обмены в обе стороны сливаются в одну связь со стрелками на обоих концах
    pairs: dict[tuple[str, str], list[dict]] = {}
    for i in ifs:
        if i["source_system"] == i["target_system"]:
            continue
        k = tuple(sorted((i["source_system"], i["target_system"]), key=lambda x: (v.tiers.get(x, 1), systems.index(x) if x in systems else 99)))
        pairs.setdefault(k, []).append(i)

    tiers: dict[int, list[str]] = {}
    for s_ in systems:
        tiers.setdefault(v.tiers.get(s_, 1), []).append(s_)
    nb: dict[str, set[str]] = {}
    for u, w in pairs:
        nb.setdefault(u, set()).add(w)
        nb.setdefault(w, set()).add(u)
    _barycenter_order(tiers, nb)

    BW, GX, GY = 170.0, 64.0, 150.0                # зазор GX: в него должен поместиться номер связи между соседями
    W = max(MIN_WIDTH + 160, max(len(r) for r in tiers.values()) * (BW + GX))
    for row, t in enumerate(sorted(tiers)):
        items = tiers[t]
        for i, s_ in enumerate(items):
            cx = W * (i + 0.5) / len(items)
            d.nodes.append(_box(s_, s_, cx - BW / 2, 60 + row * GY, BW, max_lines=2, min_h=52, accent=s_ in ("RMS", "AMR")))

    legend: dict[int, tuple[str, list[str]]] = {}
    for (u, w), lst in pairs.items():
        fwd = [i for i in lst if i["source_system"] == u]
        back = [i for i in lst if i["source_system"] == w]
        bidir = bool(fwd and back)
        src, dst = (u, w) if fwd else (w, u)
        e = Edge(src, dst, label="; ".join(([f"→ {_msg(i)}" for i in fwd] + [f"← {_msg(i)}" for i in back]) if bidir else [_msg(i) for i in lst]),
                 accent="RMS" in (u, w) and ("AMR" in (u, w) or "SAP EWM" in (u, w) or "MES" in (u, w)), bidir=bidir)
        d.edges.append(e)
        if bidir:
            legend[id(e)] = (f"{u} ↔ {w}", [f"→ " + "; ".join(_msg(i) for i in fwd), "← " + "; ".join(_msg(i) for i in back)])
        else:
            legend[id(e)] = (f"{src} → {dst}", ["; ".join(_msg(i) for i in lst)])
    _route_edges(d, d.edges)
    _finish(d, legend)
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


def _layers(keys: list[str], out_e: dict[str, set[str]]) -> tuple[dict[str, int], set[tuple[str, str]]]:
    """Слои схемы потоков. Возвратные потоки (замыкающие цикл) выделяются отдельно и слои не раздувают."""
    back: set[tuple[str, str]] = set()
    state: dict[str, int] = {}

    def dfs(a: str) -> None:
        state[a] = 1
        for b in sorted(out_e.get(a, ()), key=keys.index):
            if state.get(b) == 1:
                back.add((a, b))
            elif b not in state:
                dfs(b)
        state[a] = 2

    for k in keys:
        if k not in state:
            dfs(k)
    layer = {k: 0 for k in keys}
    for _ in range(len(keys)):
        changed = False
        for a, bs in out_e.items():
            for b in bs:
                if (a, b) not in back and layer[b] < layer[a] + 1:
                    layer[b] = layer[a] + 1
                    changed = True
        if not changed:
            break
    return layer, back


def logistics_diagram(conn: sqlite3.Connection, include_auto: bool = True) -> Diagram:
    procs = rows(conn, f"""SELECT p.code, p.name, p.from_location, p.to_location, p.cargo, p.amr_role FROM processes p
                           JOIN documents d ON d.id = p.doc_id
                           WHERE p.status IN ({_active(include_auto)}) AND d.is_current = 1 ORDER BY p.code""")
    d = Diagram("logistics", "Материальные потоки и участки роботизации", 760, 120)
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
    keys = list(labels)                                     # порядок появления сохраняется
    layer, back = _layers(keys, out_e)
    cols: dict[int, list[str]] = {}
    for k in keys:
        cols.setdefault(layer[k], []).append(k)
    nb: dict[str, set[str]] = {}
    for a, bs in out_e.items():
        for b in bs:
            nb.setdefault(a, set()).add(b)
            nb.setdefault(b, set()).add(a)
    _barycenter_order(cols, nb)

    # узкая схема читается слева направо, длинная цепочка — сверху вниз: так она остаётся в ширину страницы
    n_layers, widest = len(cols), max(len(c) for c in cols.values())
    horizontal = n_layers <= 3 and widest <= 5
    BW = 200.0
    for c, ks in cols.items():
        for r, k in enumerate(ks):
            if horizontal:
                x, y = 40 + c * (BW + 120), 60 + (r + (widest - len(ks)) / 2) * 112
            else:
                x, y = 40 + (r + (max(len(v_) for v_ in cols.values()) - len(ks)) / 2) * (BW + 56), 60 + c * 104
            d.nodes.append(_box(k, labels[k], x, y, BW, max_lines=4, min_h=52))

    merged: dict[tuple[str, str], list[dict]] = {}
    for p in flows:
        a, b = _loc_key(p["from_location"]), _loc_key(p["to_location"])
        if a != b:
            merged.setdefault(tuple(sorted((a, b), key=lambda x: (layer[x], keys.index(x)))), []).append(p)

    def tag(p: dict) -> str:
        role = {"роботизирован": " (AMR)", "планируется": " (AMR, план)", "обсуждается": " (обсуждается)"}.get(p["amr_role"], "")
        return f"{p['code']} {p['name']}{role}"

    robot_nodes: set[str] = set()
    legend: dict[int, tuple[str, list[str]]] = {}
    for (u, w), lst in merged.items():
        fwd = [p for p in lst if _loc_key(p["from_location"]) == u]
        bwd = [p for p in lst if _loc_key(p["from_location"]) == w]
        bidir = bool(fwd and bwd)
        src, dst = (u, w) if fwd else (w, u)
        robot = any(p["amr_role"] in ROBOT_ROLES for p in lst)
        talk = all(p["amr_role"] == "обсуждается" for p in lst)
        if robot:
            robot_nodes |= {u, w}
        e = Edge(src, dst, label="; ".join(dict.fromkeys(p["name"] for p in lst)), accent=robot, dashed=talk, bidir=bidir)
        d.edges.append(e)
        if bidir:
            legend[id(e)] = (f"{labels[u]} ↔ {labels[w]}", ["→ " + "; ".join(tag(p) for p in fwd), "← " + "; ".join(tag(p) for p in bwd)])
        else:
            legend[id(e)] = (f"{labels[src]} → {labels[dst]}", ["; ".join(tag(p) for p in lst)])
    for n in d.nodes:
        n.accent = n.id in robot_nodes
    _route_edges(d, d.edges)
    _finish(d, legend)
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
    d = Diagram(f"process-{p.get('code')}", f"{p.get('code')} · {p.get('name')}", 760, 160)
    if route:
        d.note = route + (f" · {p['cargo']}" if p.get("cargo") else "")
    if not steps:
        d.nodes.append(_box("only", p.get("name") or "Процесс", 40, 40, 260, accent=p.get("amr_role") in ROBOT_ROLES))
        d.height = d.nodes[0].h + 80
        return d
    actors = list(dict.fromkeys(s.get("actor") or "Другое" for s in steps))
    LW, BW, GAP = 150.0, 196.0, 30.0
    CW = BW + GAP
    d.lane_w = LW

    # колонки: следующий шаг встаёт в ту же колонку другой дорожки, если путь по вертикали свободен, иначе правее;
    # так процесс из десяти шагов занимает две-три колонки, а не десять. Порядок шагов задают номера в блоках
    lane_of = [actors.index(s.get("actor") or "Другое") for s in steps]
    col_of: list[int] = []
    occ: dict[int, set[int]] = {}
    for i, lane in enumerate(lane_of):
        col, lo, hi = 0, lane, lane
        if i:
            col = col_of[-1]
            lo, hi = sorted((lane_of[i - 1], lane))
            taken = occ.get(col, set())
            if lane in taken or any(m in taken for m in range(lo + 1, hi)):
                col, lo, hi = col + 1, lane, lane      # правее: переход идёт коленом в промежутке между колонками
        occ.setdefault(col, set()).update(range(lo, hi + 1))   # путь стрелки по вертикали тоже занят
        col_of.append(col)

    nodes = [_box(f"s{i}", f"{i + 1}. {s['action']}", LW + 16 + col_of[i] * CW, 0, BW, max_lines=8, min_h=52,
                  accent=(s.get("actor") == "AMR")) for i, s in enumerate(steps)]
    lane_h = [max(104.0, 24 + max((n.h for n, ln in zip(nodes, lane_of) if ln == k), default=0)) for k in range(len(actors))]
    y = 24.0
    for k, a_ in enumerate(actors):
        d.lanes.append(Lane(a_, y, lane_h[k]))
        y += lane_h[k]
    for n, ln in zip(nodes, lane_of):
        lane = d.lanes[ln]
        n.y = lane.y + (lane.h - 6 - n.h) / 2
    d.nodes = nodes
    d.width = LW + 16 + (max(col_of) + 1) * CW + 20
    d.height = y + 24

    for i in range(len(steps) - 1):
        a, b = nodes[i], nodes[i + 1]
        if col_of[i] == col_of[i + 1]:                        # та же колонка: прямо вверх или вниз
            pts = [(a.cx, a.y + a.h), (b.cx, b.y)] if b.y > a.y else [(a.cx, a.y), (b.cx, b.y + b.h)]
        else:
            x1, y1, x2, y2 = a.x + a.w, a.cy, b.x, b.cy
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
.dg-badge circle{fill:#ffffff;stroke:#8b88a0;stroke-width:1.4}
.dg-badge--accent circle{stroke:#6d3ce8;fill:#efe9fd}
.dg-badge text{fill:#3b3852;font-size:11px;font-weight:700}
.dg-legend text{fill:#3b3852;font-size:11.5px}
.dg-legend .dg-leg-head{fill:#15122a;font-weight:700}
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


def _svg_badge(x: float, y: float, text: str, accent: bool) -> str:
    return (f'<g class="dg-badge{" dg-badge--accent" if accent else ""}"><circle cx="{x:.1f}" cy="{y:.1f}" r="{BADGE_R:.0f}"/>'
            f'<text x="{x:.1f}" y="{y + 4:.1f}" text-anchor="middle">{escape(text)}</text></g>')


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
        mark = f"url(#{uid}-arr{'-a' if e.accent else ''})"
        out.append(f'<polyline class="{cls}" points="{pts}" marker-end="{mark}"' + (f' marker-start="{mark}"' if e.bidir else "") + "/>")
    for n in d.nodes:
        cls = "dg-node" + (" dg-node--accent" if n.accent else "")
        body = _svg_text_lines(n.cx, n.cy - (7 if n.sub else 0), n.lines)
        if n.sub:
            body += f'<text class="dg-sub" x="{n.cx:.1f}" y="{n.y + n.h - 10:.1f}" text-anchor="middle">{escape(n.sub)}</text>'
        out.append(f'<g class="{cls}" data-id="{escape(n.id)}"><rect x="{n.x:.1f}" y="{n.y:.1f}" width="{n.w:.1f}" '
                   f'height="{n.h:.1f}" rx="10"/>{body}</g>')
    for e in d.edges:
        if e.badge and e.label_at:
            out.append(_svg_badge(e.label_at[0], e.label_at[1], e.badge, e.accent))
        elif e.label:
            lines = wrap(e.label, LABEL_CHARS, 2)
            lx, ly = _label_point(e)
            w = max(len(t) for t in lines) * 6.6 + 12
            h = len(lines) * 14 + 6
            out.append(f'<g class="dg-elabel"><rect x="{lx - w / 2:.1f}" y="{ly - h / 2:.1f}" width="{w:.1f}" height="{h:.1f}" rx="6"/>'
                       + _svg_text_lines(lx, ly, lines, line_h=14) + "</g>")
    for it in d.legend:
        out.append('<g class="dg-legend">' + _svg_badge(it.x + BADGE_R, it.y + 8, it.badge, it.accent)
                   + f'<text class="dg-leg-head" x="{it.x + 2 * BADGE_R + 6:.1f}" y="{it.y + 12:.1f}">{escape(it.head)}</text>'
                   + "".join(f'<text x="{it.x + 2 * BADGE_R + 6:.1f}" y="{it.y + 12 + LEG_LINE * (k + 1):.1f}">{escape(t)}</text>'
                             for k, t in enumerate(it.lines)) + "</g>")
    out.append("</g></svg>")
    return "".join(out)


def _label_point(e: Edge) -> tuple[float, float]:
    if e.label_at:
        return e.label_at
    pts = e.points
    best = max(range(len(pts) - 1), key=lambda i: math.dist(pts[i], pts[i + 1]))
    (x1, y1), (x2, y2) = pts[best], pts[best + 1]
    return (x1 + x2) / 2, (y1 + y2) / 2


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
                 f"fontSize=11;fontStyle=1;labelBackgroundColor=#ffffff;" + ("dashed=1;" if e.dashed else "")
                 + ("startArrow=block;startFill=1;" if e.bidir else ""))
        inner = ""
        if len(e.points) > 2:
            inner = "<Array as=\"points\">" + "".join(f'<mxPoint x="{x:.0f}" y="{y:.0f}"/>' for x, y in e.points[1:-1]) + "</Array>"
        cells.append(f'<mxCell id="{page_id}-e{i}" value="{escape(e.badge or e.label)}" style="{style}" edge="1" parent="1" '
                     f'source="{ids[e.src]}" target="{ids[e.dst]}"><mxGeometry relative="1" as="geometry">{inner}</mxGeometry></mxCell>')
    for i, it in enumerate(d.legend):
        fill, stroke = ("#efe9fd", "#6d3ce8") if it.accent else ("#ffffff", "#8b88a0")
        cells.append(f'<mxCell id="{page_id}-lb{i}" value="{escape(it.badge)}" style="ellipse;whiteSpace=wrap;html=1;fillColor={fill};'
                     f'strokeColor={stroke};fontSize=11;fontStyle=1;" vertex="1" parent="1">'
                     f'<mxGeometry x="{it.x:.0f}" y="{it.y - 3:.0f}" width="22" height="22" as="geometry"/></mxCell>')
        body = f"<b>{escape(it.head)}</b>" + "".join(f"<br>{escape(t)}" for t in it.lines)
        cells.append(f'<mxCell id="{page_id}-lt{i}" value="{escape(body)}" style="text;html=1;whiteSpace=wrap;align=left;verticalAlign=top;'
                     f'fontSize=11;spacing=0;" vertex="1" parent="1">'
                     f'<mxGeometry x="{it.x + 28:.0f}" y="{it.y - 3:.0f}" width="{(d.width - 2 * MARGIN) / (2 if len(d.legend) > 3 and d.width >= 700 else 1) - 30:.0f}" '
                     f'height="{LEG_LINE * (len(it.lines) + 1):.0f}" as="geometry"/></mxCell>')
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
        if e.bidir:
            arrow = "<-.->" if e.dashed else ("<==>" if e.accent else "<-->")
        else:
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


def to_png(d: Diagram, path: Path, scale: float = 2.0, with_title: bool = False, with_legend: bool = True) -> Path | None:
    """Рисует схему в PNG. Возвращает путь или None, если нет Pillow или шрифта с кириллицей.
    with_title — добавить заголовок и маршрут сверху (для отдельных файлов; в Word заголовок уже есть в тексте).
    with_legend=False — только схема с номерами, без расшифровки (в Word она идёт обычной таблицей)."""
    if not png_available() or d.empty:
        return None
    from PIL import Image, ImageDraw

    s = scale
    head = (40 if d.note else 30) if with_title else 0
    f_node = _font(FONT_BOLD_CANDIDATES, int(12 * s)) or _font(FONT_CANDIDATES, int(12 * s))
    f_small = _font(FONT_CANDIDATES, int(11 * s))
    f_leg = _font(FONT_CANDIDATES, int(11.5 * s))
    f_leg_b = _font(FONT_BOLD_CANDIDATES, int(11.5 * s)) or f_leg
    f_title = _font(FONT_BOLD_CANDIDATES, int(15 * s)) or f_node
    height = d.height if with_legend else d.plot_height
    img = Image.new("RGB", (int(d.width * s), int((height + head) * s)), "white")
    dr = ImageDraw.Draw(img)
    P = lambda x, y: (x * s, (y + head) * s)  # noqa: E731

    def text_center(cx, cy, lines, font, fill, lh):
        top = cy - (len(lines) - 1) * lh / 2
        for i, t in enumerate(lines):
            dr.text(P(cx, top + i * lh), t, font=font, fill=fill, anchor="mm")

    def arrow(tip, frm, color):
        ang = math.atan2(tip[1] - frm[1], tip[0] - frm[0])
        a = 9 * s
        dr.polygon([tip, (tip[0] - a * math.cos(ang - 0.42), tip[1] - a * math.sin(ang - 0.42)),
                    (tip[0] - a * math.cos(ang + 0.42), tip[1] - a * math.sin(ang + 0.42))], fill=color)

    def badge(x, y, text, accent):
        r = BADGE_R * s
        cx, cy = P(x, y)
        dr.ellipse([cx - r, cy - r, cx + r, cy + r], fill="#efe9fd" if accent else "white",
                   outline="#6d3ce8" if accent else "#8b88a0", width=max(1, int(1.4 * s)))
        dr.text((cx, cy), text, font=f_small, fill="#3b3852", anchor="mm")

    if with_title:
        dr.text((16 * s, 22 * s), d.title, font=f_title, fill="#15122a", anchor="lm")
        if d.note:
            dr.text((16 * s, 44 * s), d.note[:150], font=f_small, fill="#5b5873", anchor="lm")
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
        arrow(pts[-1], pts[-2], color)
        if e.bidir:
            arrow(pts[0], pts[1], color)
    for n in d.nodes:
        fill, outline = ("#efe9fd", "#6d3ce8") if n.accent else ("#ffffff", "#c3c0d3")
        dr.rounded_rectangle([P(n.x, n.y), P(n.x + n.w, n.y + n.h)], radius=10 * s, fill=fill, outline=outline,
                             width=int((1.8 if n.accent else 1.2) * s))
        text_center(n.cx, n.cy, n.lines, f_node, "#15122a", LINE_H)
    for e in d.edges:
        if e.badge and e.label_at:
            badge(e.label_at[0], e.label_at[1], e.badge, e.accent)
        elif e.label:
            lines = wrap(e.label, LABEL_CHARS, 2)
            lx, ly = _label_point(e)
            w = max(dr.textlength(t, font=f_small) for t in lines) / s + 12
            h = len(lines) * 14 + 6
            dr.rounded_rectangle([P(lx - w / 2, ly - h / 2), P(lx + w / 2, ly + h / 2)], radius=6 * s, fill="white")
            text_center(lx, ly, lines, f_small, "#5b5873", 14)
    for it in (d.legend if with_legend else []):
        badge(it.x + BADGE_R, it.y + 8, it.badge, it.accent)
        dr.text(P(it.x + 2 * BADGE_R + 6, it.y + 8), it.head, font=f_leg_b, fill="#15122a", anchor="lm")
        for k, t in enumerate(it.lines):
            dr.text(P(it.x + 2 * BADGE_R + 6, it.y + 8 + LEG_LINE * (k + 1)), t, font=f_leg, fill="#3b3852", anchor="lm")
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
