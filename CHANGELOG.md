# Job Tracker — Changelog / Журнал изменений

## Правила работы с кодом

1. **`static/index.html`** — ЗАПРЕЩЕНО менять без одобрения пользователя
2. **Новый функционал** — только в `static/js/*.js`, `static/css/*.css`
3. **Перед изменением** — показать план и получить одобрение
4. **После изменения** — записать в этот журнал

---

## 2026-01-24

### [20:30] Complete Application Preparation Modal Rewrite

#### Что сделано:
- **Полная переписка модалки анализа вакансий**
- Two-panel layout: Analysis (left) + Documents (right)
- AI-powered analysis via Claude API
- Automatic CV optimization decision
- Cover letter generation
- Direct links to open documents

#### Новые файлы:
- `api/__init__.py` - новый модуль API
- `api/prepare_application.py` - комплексная подготовка заявки
- `.env` - ANTHROPIC_API_KEY, JOB_TRACKER_ENV

#### Новые endpoints:
- `POST /prepare-application` - AI анализ + CV + Cover Letter
- `GET /open-file/{file_type}` - открыть файл в системе

#### Изменённые файлы:
- `main.py` - новые endpoints, dotenv loading
- `static/index.html` - новая модалка, новые JS функции
- `browser/v5/engine.py` - TEXT_DEFAULTS, re-scan logic, FormLogger import

#### Новые функции в `api/prepare_application.py`:
- `analyze_job_with_ai()` - Claude-powered JD analysis
- `generate_cover_letter()` - AI cover letter generation
- `create_optimized_cv()` - CV с добавленными keywords
- `find_application_url()` - поиск URL формы

#### V5 Engine improvements:
- `TEXT_DEFAULTS` - salary, years of experience defaults
- Extended `YES_NO_PATTERNS` 
- `find_text_default()` method
- `_scan_for_new_fields()` - обнаружение новых полей после выбора
- Re-scan loop (до 5 итераций) для динамических форм

#### Dependencies:
- python-dotenv
- beautifulsoup4 (bs4)
- python-docx

#### Статус: 🔄 Testing

---

## 2026-01-23 19:15 - Fix storage unification (jobs.json → jobs_new.json)

### Problem
- `/onboard` endpoint saved to `jobs.json` via `job_storage.py`
- UI and `/stats` read from `jobs_new.json` via `pipeline_storage.py`
- TEKsystems job added via onboard was invisible in UI

### Solution
- Changed `storage/job_storage.py` to use `jobs_new.json` instead of `jobs.json`
- Applied to both PROD and DEV
- Migrated TEKsystems job from `jobs.json` to `jobs_new.json`

### Files changed
- `storage/job_storage.py` (PROD + DEV): `JOBS_FILE = DATA_DIR / "jobs_new.json"`

### Result
- All storage operations now unified on `jobs_new.json`
- TEKsystems job now visible in UI ✅
- Total pipeline: 1078 jobs
