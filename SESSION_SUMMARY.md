# Job Tracker — Session Summary (Dec 23, 2024)

## Текущая проблема

**UI загружается медленно (5-10 сек)** при старте.

Причина: загрузка 7460 jobs из кэша и обработка в браузере:
- `/jobs?profile=all` возвращает 8MB JSON
- JavaScript обрабатывает 7460 jobs (enrichJobsLocationNorm, applyFilters, renderJobs)

Это происходит **локально на ноутбуке** — нет сетевых задержек, проблема в объёме данных.

## Что сделано сегодня

### 1. My Location / My Roles toggles
- Два toggle в header (оба OFF по умолчанию)
- My Roles: фильтрует Product/TPM/Program (506 jobs)
- My Location: фильтрует 5 штатов + Remote USA (54 jobs)
- Работают на обоих табах

### 2. Ashby ATS интеграция
- Новый парсер `/parsers/ashby.py`
- Добавлены: OpenAI, Snowflake, Confluent, Vanta, Sentry, Zapier, Notion, Ramp, Linear
- +1131 jobs от Ashby компаний

### 3. Исправлены ATS ошибки
- Chime, Webflow → Greenhouse (были Lever 404)
- Atlassian, Bosch, SoFi → исправлены SmartRecruiters URLs
- 18 компаний помечены `enabled: false, status: "ats_404"`

### 4. UI грузит из кэша
- `/jobs?profile=all` использует кэш (cache/jobs_all.json)
- Добавлен GZipMiddleware для сжатия
- `loadJobsFromCache()` вместо `loadJobsFromPipeline()`

### 5. Unified header с воронкой
- Total → Role → US → My Area → Shown

## Структура проекта

```
job-tracker/
├── main.py                    # FastAPI backend
├── static/
│   └── index.html             # Frontend (vanilla JS)
├── parsers/
│   ├── greenhouse.py
│   ├── lever.py
│   ├── smartrecruiters.py
│   └── ashby.py               # NEW
├── data/
│   ├── companies.json         # 80 компаний (62 enabled)
│   ├── jobs_new.json          # Pipeline: NEW jobs (520)
│   ├── jobs_pipeline.json     # Pipeline: ACTIVE jobs
│   └── jobs_archive.json      # Pipeline: ARCHIVED jobs
├── cache/
│   └── jobs_all.json          # Кэш всех jobs (7460, 8MB)
├── config/
│   ├── roles.json             # Определения ролей
│   ├── industries.json
│   └── settings.json
├── utils/
│   ├── cache_manager.py       # TTL 6 часов
│   ├── normalize.py
│   └── job_utils.py
└── storage/
    └── pipeline_storage.py    # NEW/ACTIVE/ARCHIVE lifecycle
```

## Ключевые числа

- **Total jobs в кэше:** 7460
- **Relevant roles (Product/TPM/Program):** 520
- **My Location (5 states + Remote USA):** 54
- **Companies:** 80 total, 62 enabled, 18 disabled (ats_404)

## Варианты оптимизации загрузки

1. **Lazy loading** — грузить только 520 relevant jobs сначала, остальные по требованию
2. **Virtual scrolling** — не рендерить все 7460 строк в DOM
3. **Серверная фильтрация** — backend отдаёт только нужные jobs
4. **Кэш в браузере** — IndexedDB/localStorage для повторных визитов

## Команды

```bash
# Запуск сервера
cd /Users/antonkondakov/projects/job-tracker
source .venv/bin/activate
uvicorn main:app --workers 4

# Проверка кэша
curl -s "http://localhost:8000/cache/info?cache_key=all"

# Проверка jobs
curl -s "http://localhost:8000/jobs?profile=all" | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d['jobs']))"
```
