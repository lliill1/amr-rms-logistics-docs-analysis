"""Командная строка — удобно для долгих задач (например, извлечение требований на ночь).

    python scripts/cli.py status
    python scripts/cli.py process "D:\\Projects\\AMR\\archive"
    python scripts/cli.py embed
    python scripts/cli.py extract --limit 200
    python scripts/cli.py extract --kinds processes integration
    python scripts/cli.py conflicts
    python scripts/cli.py ask "Какая система создаёт транспортное задание для AMR?"
    python scripts/cli.py report --draft
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import db, pipeline, report  # noqa: E402
from core.ask import ask, source_label  # noqa: E402
from core.config import load_settings  # noqa: E402
from core import consolidate  # noqa: E402
from core.facts import ALL_KINDS, run_extraction  # noqa: E402
from core.llm import OllamaClient  # noqa: E402


def _progress(stage: str, done: int, total: int) -> None:
    print(f"\r{stage}: {done}/{total}", end="" if done < total else "\n", flush=True)


def main() -> None:
    p = argparse.ArgumentParser(description="DocAnalyzer AMR/RMS")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="проверить Ollama и базу")
    sp = sub.add_parser("process", help="загрузить и разобрать папку или архив")
    sp.add_argument("source")
    sp.add_argument("--no-llm", action="store_true", help="без эмбеддингов")
    sub.add_parser("embed", help="досчитать эмбеддинги")
    se = sub.add_parser("extract", help="извлечь факты: требования, процессы, обмены и ответственность")
    se.add_argument("--limit", type=int, default=0, help="максимум фрагментов за запуск (0 — все)")
    se.add_argument("--kinds", nargs="+", choices=ALL_KINDS, default=ALL_KINDS, help="какие виды фактов извлекать")
    sub.add_parser("conflicts", help="противоречия и пробелы")
    sa = sub.add_parser("ask", help="задать вопрос")
    sa.add_argument("question")
    sr = sub.add_parser("report", help="выгрузить Word и Excel")
    sr.add_argument("--draft", action="store_true", help="включить непроверенные требования")
    args = p.parse_args()

    settings = load_settings()
    conn = db.connect(settings.db_file)
    llm = OllamaClient.from_settings(settings)
    st = llm.status()

    if args.cmd == "status":
        print("База:", settings.db_file)
        print("Документов:", db.one(conn, "SELECT count(*) n FROM documents")["n"])
        print("Ollama:", "OK" if st["ok"] else (st["error"] or "нет моделей: " + ", ".join(st["missing"])))
        return

    if args.cmd == "process":
        stats = pipeline.run_all(conn, settings, Path(args.source), llm=None if args.no_llm or not st["ok"] else llm,
                                 progress=_progress)
        print({k: v for k, v in stats.__dict__.items() if k not in ("skipped", "messages")})
        for m in stats.messages:
            print("!", m)
        for path, reason in stats.skipped:
            print("пропущен:", path, "—", reason)
        return

    if not st["ok"] and args.cmd in ("embed", "extract"):
        sys.exit("Ollama недоступна или не скачаны модели: " + (st["error"] or ", ".join(st["missing"])))

    if args.cmd == "embed":
        print("Посчитано:", pipeline.build_embeddings(conn, llm, _progress))
    elif args.cmd == "extract":
        stats = run_extraction(conn, settings, llm, kinds=args.kinds, limit=args.limit or None,
                               progress=lambda done, total: _progress("Извлечение", done, total))
        for kind, st in stats.pop("by_kind").items():
            print(f"{kind}: найдено {st['found']}, принято {st['accepted']}, отброшено {st['rejected_quotes']}, ошибок {st['errors']}")
        print(stats)
    elif args.cmd == "conflicts":
        items = consolidate.refresh(conn)
        for c in items:
            print(f"#{c['id']} [{c['kind_label']}, {c['status_label']}] {c['title']}")
            for v in c["details"].get("variants", []):
                print(f"    {v['value']}: " + "; ".join(s["label"] for s in v["sources"]))
        print(f"Всего: {len(items)}, неразобранных: {sum(c['status'] == 'open' for c in items)}")
    elif args.cmd == "ask":
        ans = ask(conn, settings, args.question, llm=llm if st["ok"] else None)
        print(ans.text or ans.error or "(модель недоступна — только фрагменты)")
        print()
        for n, h in enumerate(ans.hits, start=1):
            print(f"[{n}] {source_label(h)}")
    elif args.cmd == "report":
        print(report.build_docx(conn, settings.output_dir, include_auto=args.draft))
        print(report.build_xlsx(conn, settings.output_dir))
        print(report.build_diagrams_zip(conn, settings.output_dir, include_auto=args.draft))


if __name__ == "__main__":
    main()
