# Job Tracker — Архитектура проекта

> Автоматически сгенерировано: 2024-12-22

## Назначение

Агрегатор вакансий для **Product/Program Manager** ролей с фокусом на **North Carolina** и Remote-USA позиции.

---

## Структура проекта

```
job-tracker/
├── main.py              # FastAPI приложение (основной файл)
├── storage.py           # Работа с профилями и статусами
├── requirements.txt     # Зависимости
│
├── parsers/             # Парсеры ATS
│   ├── greenhouse.py    # ✅ Полная поддержка
│   ├── lever.py         # ✅ Полная поддержка
│   ├── smartrecruiters.py # ✅ Поддержка
│   ├── workday.py       # ⚠️ Частичная поддержка
│   └── workday_json.py
│
├── utils/
│   ├── normalize.py     # Нормализация локаций + classify_role()
│   ├── cache_manager.py # Кэш с TTL 6 часов
│   └── role_classifier_rules.py
│
├── config/
│   ├── roles.json       # Целевые роли и ключевые слова
│   ├── settings.json    # Настройки (кэш, AI)
│   └── industries.json  # Категории индустрий
│
├── data/
│   └── companies.json   # Мастер-список компаний (80 шт.)
│
├── static/
│   └── index.html       # Фронтенд (vanilla JS, dark theme)
│
├── cache/               # Автогенерируемый кэш (gitignore)
├── profiles/            # Профили фильтрации
└── tests/               # Тесты
```

---

## Компоненты

### 1. main.py — FastAPI приложение

**Ключевые функции:**
- `_fetch_for_company()` — вызывает парсер ATS, добавляет метаданные
- `compute_geo_bucket_and_score()` — гео-скоринг
- `compute_job_key()` — уникальный ключ для статуса

**Гео-скоринг:**
```python
TARGET_STATE = "NC"
NEIGHBOR_STATES = {"VA", "SC", "GA", "TN"}
LOCAL_CITIES = {"raleigh", "durham", "cary", "chapel hill", "morrisville"}

# Баллы:
# local (NC city) = 100
# nc (NC state) = 80  
# neighbor = 60
# remote_usa = 50
# other = 0
```

### 2. Парсеры ATS

| ATS | Файл | API |
|-----|------|-----|
| Greenhouse | `greenhouse.py` | `boards-api.greenhouse.io/v1/boards/{company}/jobs` |
| Lever | `lever.py` | `api.lever.co/v0/postings/{company}` |
| SmartRecruiters | `smartrecruiters.py` | Публичный API |
| Workday | `workday.py` | Частичная поддержка |

### 3. Нормализация (`utils/normalize.py`)

**`normalize_location(location)`:**
- Парсит строку локации
- Определяет: city, state, remote, remote_scope
- Поддержка форматов: "Raleigh, NC", "Remote - USA", "New York, NY / Remote"

**`classify_role(title, description)`:**
- Классифицирует роль по ключевым словам
- Категории: product, tpm_program, project, other
- Фильтрует негативные роли (engineers, sales, etc.)

### 4. Кэширование (`utils/cache_manager.py`)

- **TTL:** 6 часов
- **Файлы:** `cache/jobs_{profile}.json`
- **Функции:** `load_cache()`, `save_cache()`, `clear_cache()`

### 5. Storage (`storage.py`)

- `load_profile(name)` — загружает компании по профилю/тегам
- `load_companies_master()` — мастер-список компаний
- Статусы хранятся в `job_status.json`

---

## Конфигурация

### config/settings.json
```json
{
  "cache": {
    "enabled": true,
    "ttl_hours": 6
  },
  "ai": {
    "enabled": false,
    "use_for_ambiguous_only": true
  }
}
```

### config/roles.json

Целевые роли:
- Product Manager (priority: 10)
- Technical Program Manager (priority: 9)
- Product Owner (priority: 9)
- Program Manager (priority: 8)
- Project Manager (priority: 7)
- Scrum Master (priority: 6)

Skip-роли: engineers, designers, QA, sales, non-IT

---

## Профили фильтрации

| Профиль | Теги |
|---------|------|
| `all` | Все компании |
| `fintech` | fintech, payments, banking, crypto, trading, cards |
| `banking` | bank, banking, finserv |
| `saas` | saas, enterprise |
| `security` | security, infosec, identity |

---

## Статусы заявок

| Статус | Описание |
|--------|----------|
| `New` | Новая вакансия |
| `Applied` | Подана заявка |
| `Answered` | Получен ответ |
| `Rejected` | Отказ |

---

## Данные

### companies.json (80 компаний)

```json
{
  "id": "brex",
  "name": "Brex",
  "ats": "greenhouse",
  "board_url": "https://boards.greenhouse.io/brex",
  "tags": ["fintech", "card", "us"],
  "industry": "Fintech",
  "priority": 0,
  "hq_state": null,
  "region": "us"
}
```

---

## Фронтенд

`static/index.html` — single-page приложение:
- Vanilla JS (без фреймворков)
- Dark theme
- Фильтры: штаты, роли, ATS, поиск
- Статусы заявок с сохранением

---

## Job Data Contract (Schema)

Все парсеры следуют единому контракту данных (`parsers/schema.py`):

### RawJob (от парсера)

| Поле | Тип | Статус | Описание |
|------|-----|--------|----------|
| `title` | str | **Required** | Название позиции |
| `url` | str | **Required** | Ссылка на вакансию |
| `ats_job_id` | str | Expected | ID в ATS (для дедупликации) |
| `location` | str | Expected | Сырая строка локации |
| `updated_at` | str | Expected | ISO datetime обновления |
| `department` | str | Optional | Отдел/команда |
| `first_published` | str | Optional | Дата публикации |
| `description` | str | Optional | Текст описания |

### EnrichedJob (после обработки)

Pipeline (`main.py`) автоматически добавляет:

| Поле | Описание |
|------|----------|
| `id` | Уникальный hash (company+ats_job_id+url) |
| `company` | Название компании |
| `ats` | Тип ATS |
| `location_norm` | Нормализованная локация |
| `geo_bucket` | local/nc/neighbor/remote_usa/other |
| `geo_score` | 0-100 для ранжирования |
| `role_family` | product/tpm_program/project/other |
| `role_category` | primary/adjacent/excluded/unknown |
| `company_data` | {priority, hq_state, region, tags} |

### Data Flow

```
┌─────────────┐     RawJob      ┌─────────────┐    EnrichedJob   ┌─────────┐
│   Parser    │ ───────────────▶│   main.py   │ ─────────────────▶│  Cache  │
│ (ATS-spec)  │   title, url,   │  _fetch_    │   + id, company, │  .json  │
│             │   ats_job_id,   │  for_company│   location_norm, │         │
│             │   location,     │             │   role_family,   │         │
│             │   updated_at    │             │   geo_bucket...  │         │
└─────────────┘                 └─────────────┘                  └─────────┘
                                      │
                                      ▼
                               ┌─────────────┐
                               │  Pipeline   │
                               │  Storage    │
                               │ jobs_new.json│
                               └─────────────┘
```

### Adding New Parser

1. Создать `parsers/newats.py`
2. Реализовать `fetch_newats(company, board_url) -> List[RawJob]`
3. Вернуть dict с required полями: `title`, `url`
4. Добавить в `main.py` вызов в `_fetch_for_company()`
5. Добавить ATS в companies.json

