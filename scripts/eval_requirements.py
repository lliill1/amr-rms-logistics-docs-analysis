"""Оценка качества извлечения требований по эталонной разметке аналитика.

    python scripts/eval_requirements.py eval/requirements_gold.csv
    python scripts/eval_requirements.py eval/requirements_gold.csv --details

Формат CSV (разделитель «;», первая строка — заголовок):
    doc_rel_path;page;requirement;target
    02_EWM/ПР_EWM_v2.3.docx;34;RMS принимает транспортные задания от EWM;RMS

Сравниваются только документы, которые есть в эталоне. Требование модели считается найденным верно,
если его формулировка достаточно близка к эталонной (общие слова по основам, порог --threshold).
Отклонённые аналитиком требования не учитываются.

Точность (precision) — какая доля найденного моделью есть в эталоне.
Полнота (recall)     — какая доля эталона найдена моделью.
Запускайте перед сменой модели или промпта и сравнивайте числа.
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import db  # noqa: E402
from core.config import load_settings  # noqa: E402

STOP = set("и в во на с со к по от для из что как не или а также должен должна должно должны быть".split())


def stems(text: str) -> set[str]:
    words = re.findall(r"[a-zа-яё0-9]+", str(text).lower().replace("ё", "е"))
    return {w[:5] for w in words if w not in STOP and len(w) > 1}


def similarity(a: str, b: str) -> float:
    sa, sb = stems(a), stems(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / min(len(sa), len(sb))


def doc_key(path: str) -> str:
    """Сравниваем по имени файла: путь в эталоне может отличаться папкой."""
    return PurePosixPath(str(path).replace("\\", "/")).name.lower()


def load_gold(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8-sig")
    rows = list(csv.DictReader(text.splitlines(), delimiter=";"))
    need = {"doc_rel_path", "requirement"}
    if not rows or not need <= set(rows[0]):
        sys.exit(f"В {path} нужны столбцы: doc_rel_path;page;requirement;target")
    return [r for r in rows if (r.get("requirement") or "").strip()]


def evaluate(conn, gold: list[dict], threshold: float = 0.6) -> dict:
    gold_docs = {doc_key(g["doc_rel_path"]) for g in gold}
    found = [r for r in db.rows(conn, """SELECT r.code, r.text, r.target, d.rel_path FROM requirements r
                                         JOIN documents d ON d.id = r.doc_id WHERE r.status != 'rejected'""")
             if doc_key(r["rel_path"]) in gold_docs]
    matched_gold: set[int] = set()
    matched_found: set[int] = set()
    pairs = []
    for fi, f in enumerate(found):
        best, best_gi = 0.0, None
        for gi, g in enumerate(gold):
            if doc_key(g["doc_rel_path"]) != doc_key(f["rel_path"]) or gi in matched_gold:
                continue
            s = similarity(f["text"], g["requirement"])
            if s > best:
                best, best_gi = s, gi
        if best_gi is not None and best >= threshold:
            matched_gold.add(best_gi)
            matched_found.add(fi)
            pairs.append((f, gold[best_gi], best))
    precision = len(matched_found) / len(found) if found else 0.0
    recall = len(matched_gold) / len(gold) if gold else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"gold": len(gold), "found": len(found), "matched": len(pairs), "precision": precision, "recall": recall,
            "f1": f1, "pairs": pairs,
            "missed": [g for gi, g in enumerate(gold) if gi not in matched_gold],
            "extra": [f for fi, f in enumerate(found) if fi not in matched_found]}


def main() -> None:
    ap = argparse.ArgumentParser(description="Оценка извлечения требований по эталону")
    ap.add_argument("gold", type=Path)
    ap.add_argument("--threshold", type=float, default=0.6, help="порог похожести формулировок, 0..1")
    ap.add_argument("--details", action="store_true", help="показать пропущенные и лишние требования")
    args = ap.parse_args()
    settings = load_settings()
    conn = db.connect(settings.db_file)
    res = evaluate(conn, load_gold(args.gold), args.threshold)
    print(f"Эталон: {res['gold']}, найдено моделью (в тех же документах): {res['found']}, совпало: {res['matched']}")
    print(f"Точность: {res['precision']:.0%}   Полнота: {res['recall']:.0%}   F1: {res['f1']:.0%}")
    if args.details:
        print("\nПропущено моделью:")
        for g in res["missed"]:
            print(f"  - [{doc_key(g['doc_rel_path'])}] {g['requirement']}")
        print("\nНет в эталоне (лишнее или эталон неполный):")
        for f in res["extra"]:
            print(f"  - {f['code']} [{doc_key(f['rel_path'])}] {f['text']}")


if __name__ == "__main__":
    main()
