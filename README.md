# amr-rms-logistics-docs-analysis

Анализ проектной документации заказчика и подрядчика с целью определения роли AMR/RMS в логистических процессах предприятия. Исходные материалы: архив проектной документации по складской и производственной логистике, внедрению SAP EWM, интеграции ERP/MES, описания бизнес-процессов, схемы товаропотоков, проектные решения и протоколы совещаний.

**DocAnalyzer AMR/RMS** — локальное приложение для этого анализа. Документы не покидают компьютер: текст извлекается на месте, а языковая модель работает через [Ollama](https://ollama.com) на этой же машине.

## Что умеет версия 2

Основной интерфейс — веб-версия (`run_web.bat`), пять экранов:

| Экран | Что делает |
|---|---|
| **Архив и обработка** | Принимает папку или zip (в том числе вложенные архивы и русские имена файлов из Windows). Извлекает текст из docx, pdf, xlsx, txt, eml; старые .doc/.xls/.ppt/.vsd конвертирует через LibreOffice. Составляет карточку каждого документа: тип, дату, редакцию, статус, сторону, подсистемы, релевантность AMR/RMS. Находит редакции одного документа и помечает устаревшие. Карточки можно править вручную. |
| **Вопросы к архиву** | Гибридный поиск (по словам + по смыслу) и ответ модели строго по найденным фрагментам, со ссылками на документ и страницу; не больше двух фрагментов из одного документа. Найденную проблему можно одной кнопкой отправить в открытые вопросы. |
| **Факты и ревью** | Модель извлекает три вида фактов: **требования** к AMR/RMS, **процессы** (триггер, инициатор, шаги, откуда/куда, роль роботов) и **обмены и ответственность** (кто кому что передаёт и по какому протоколу, какая система отвечает за функцию). У каждого факта — дословная цитата, проверяемая по тексту документа. Вкладка **«Противоречия»**: документы называют разные системы для одной функции, решение изменилось между редакциями или не найдено вовсе — принять вариант, вынести вопрос заказчику или отметить «не противоречие». |
| **Логистика и системы** *(новое)* | Схема взаимодействия систем, карточки процессов с картой шагов по исполнителям, общая схема материальных потоков с выделением участков AMR, матрица ответственности «функция × система». Всё строится из фактов; переключатель «Только подтверждённое» показывает чистовой вариант. |
| **Отчёт** | Word по «Образу результата»: обзор архива, схема логистики, карта процессов, схема систем, матрица ответственности и требования, противоречия, пробелы и вопросы. Excel со всеми таблицами. Архив схем в редактируемом виде: draw.io (все схемы страницами одного файла), SVG, Mermaid, PNG. |

Что нового по сравнению с версией 1 — в [CHANGELOG.md](CHANGELOG.md), устройство — в [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Установка (Windows)

1. **Python 3.12** — [python.org](https://www.python.org/downloads/). При установке отметьте «Add python.exe to PATH».
2. **Ollama** — [ollama.com/download](https://ollama.com/download). После установки в командной строке:
   ```
   ollama pull qwen3:8b
   ollama pull bge-m3
   ```
   Модель `qwen3:8b` занимает около 5 ГБ и работает на ноутбуке с 16–32 ГБ памяти. Если есть видеокарта NVIDIA с 8 ГБ и больше, можно попробовать `qwen3:14b`: она точнее, но медленнее. Модель меняется в `config/settings.yaml` или `config/settings.local.yaml`.
3. **LibreOffice** (по желанию, для старых форматов .doc/.xls/.ppt/.vsd) — [libreoffice.org](https://www.libreoffice.org/download/).
4. Двойной щелчок по **`run_web.bat`** (или `.\run_web.bat` в терминале). При первом запуске он создаст окружение и установит зависимости, затем откроет приложение в браузере (адрес `http://127.0.0.1:8501`). Когда меняется `requirements.txt`, недостающие библиотеки доустанавливаются сами. Старый интерфейс на Streamlit по-прежнему запускается через `run.bat`, но новых экранов версии 2 в нём нет.

Без Ollama приложение тоже работает: загрузка, каталог и поиск по словам доступны. Ответы модели и извлечение требований появятся, когда Ollama будет запущена.

## Веб-интерфейс и API

Логика вынесена за тонкий HTTP-слой (`api/`, FastAPI). Долгие операции (обработка архива, эмбеддинги, извлечение требований) идут фоновыми задачами с прогрессом; одновременно выполняется одна.

Запуск: двойной щелчок по **`run_web.bat`** или `python server.py` (адрес `http://127.0.0.1:8501`, документация всех маршрутов — `/api/docs`). Статический фронтенд кладётся в `web/` и отдаётся с корневого адреса.

| Группа | Маршруты |
|---|---|
| Служебное | `GET /api/status`, `GET /api/meta`, `GET /api/jobs/{id}`, `GET /api/jobs/current` |
| Архив | `POST /api/archive/process` (путь), `POST /api/archive/upload` (zip), `POST /api/archive/embeddings`, `GET /api/archive/summary`, `GET /api/documents`, `PATCH /api/documents/{id}` |
| Вопросы | `POST /api/ask` |
| Факты | `GET /api/extraction/pending`, `POST /api/extraction/run` (`{"limit": 20, "kinds": ["requirements", "processes", "integration"]}`), `GET /api/requirements`, `GET`/`PATCH /api/requirements/{id}`, `POST /api/requirements/confirm`, `GET`/`POST /api/open-questions`, `PATCH`/`DELETE /api/open-questions/{id}` |
| Логистика и системы *(v2)* | `GET /api/processes`, `GET`/`PATCH /api/processes/{id}`, `POST /api/processes/confirm`; то же для `/api/interfaces` и `/api/responsibilities`; `GET /api/model/matrix`, `GET /api/model/overview`; схемы `GET /api/diagrams/systems.svg`, `/api/diagrams/logistics.svg`, `/api/diagrams/process/{id}.svg`, `/api/diagrams/mermaid` |
| Противоречия *(v2)* | `GET /api/conflicts`, `POST /api/conflicts/{id}/resolve` (`{"value": "SAP EWM"}`), `/question`, `/dismiss`, `/reopen` |
| Отчёт | `GET /api/report/summary`, `POST /api/report/docx`, `POST /api/report/xlsx`, `POST /api/report/diagrams` *(v2)*, `GET /api/report/download/{имя}` |

Старый интерфейс (`app.py`, `ui/`) оставлен рядом и работает как раньше, но экранов версии 2 в нём нет.

## Быстрая проверка на вымышленных данных

```
.venv\Scripts\activate
python scripts\make_demo_archive.py
```

Затем на экране «Архив и обработка» укажите путь `data\demo_archive`.

## Командная строка

Удобна для долгих задач, например чтобы оставить извлечение требований на ночь:

```
python scripts\cli.py status
python scripts\cli.py process "D:\Projects\AMR\archive"
python scripts\cli.py extract --limit 200
python scripts\cli.py extract --kinds processes integration
python scripts\cli.py conflicts
python scripts\cli.py ask "Какая система создаёт транспортное задание для AMR?"
python scripts\cli.py report --draft
python scripts\eval_requirements.py eval\requirements_gold.csv --details
```

## Структура

```
app.py                 точка входа Streamlit (старый интерфейс)
ui/                    экраны Streamlit
server.py              запуск веб-версии (FastAPI + web/)
api/                   тонкий HTTP-слой над core/
web/                   статический фронтенд (HTML/CSS/JS)
core/
  ingest.py            распаковка архивов, конвертация LibreOffice, дубликаты
  parsers/             docx, pdf, xlsx, txt/csv, eml
  catalog.py           карточки документов и семейства редакций
  glossary.py          доменный глоссарий (config/glossary.yaml)
  index.py             фрагменты, полнотекстовый (FTS5) и векторный поиск
  llm.py               клиент Ollama
  ask.py               ответы на вопросы со ссылками
  extract.py           извлечение требований с проверкой цитат
  facts.py             v2: общий механизм извлечения (требования, процессы, обмены и ответственность)
  vocab.py             v2: справочник систем и функций (config/model.yaml)
  consolidate.py       v2: матрица ответственности, противоречия, пробелы
  diagrams.py          v2: схемы — SVG, draw.io, Mermaid, PNG
  report.py            Word и Excel
  pipeline.py          конвейер обработки
config/                settings.yaml, glossary.yaml, model.yaml (v2), prompts/
scripts/               cli.py, make_demo_archive.py, eval_requirements.py (v2)
tests/                 тесты (без Ollama, с поддельной моделью)
data/                  база и рабочие файлы — НЕ попадает в git
out/                   выгруженные отчёты — НЕ попадает в git
```

## Настройка под проект

- `config/glossary.yaml` — термины и синонимы предметной области. Добавляйте названия участков, цехов, систем и сокращения, принятые у заказчика.
- `config/settings.yaml` → `catalog.customer_markers` / `contractor_markers` — названия компаний заказчика и подрядчика. По ним определяется сторона-автор документа.
- `config/model.yaml` *(v2)* — системы (и как они называются в документах), функции для матрицы ответственности (какие обязательны), роли роботов, обязательные обмены. По нему ищутся пробелы.
- `config/prompts/` — тексты запросов к модели.
- Свой шаблон Word: положите `templates/report.docx`. Отчёт возьмёт из него стили.

## Тесты

```
python -m pytest -q
```

## Оценка качества извлечения

Разметьте вручную требования в 5–10 документах (`eval/requirements_gold.csv`, образец — `eval/requirements_gold.example.csv` для демо-архива) и запускайте `python scripts\eval_requirements.py eval\requirements_gold.csv` перед сменой модели или промпта.

## Конфиденциальность

- Документы заказчика храните только в `data/` или вне репозитория. Папки `data/` и `out/` исключены из git.
- Приложение слушает только `127.0.0.1`, поэтому с других компьютеров недоступно.
- Запросы идут только к Ollama по адресу из настроек (по умолчанию `localhost`).
