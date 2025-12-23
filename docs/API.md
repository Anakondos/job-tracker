# Job Tracker — API Reference

> Базовый URL: `http://localhost:8000`

---

## Эндпоинты

### GET /health

Проверка состояния сервера.

**Response:**
```json
{"status": "ok"}
```

---

### GET /jobs

Получить список вакансий с фильтрами.

**Query параметры:**

| Параметр | Тип | По умолчанию | Описание |
|----------|-----|--------------|----------|
| `profile` | string | `"all"` | Профиль: all, fintech, banking, saas, security |
| `ats_filter` | string | `"all"` | ATS: all, greenhouse, lever, smartrecruiters |
| `role_filter` | string | `"all"` | Роль: all, product, tpm_program, project, other |
| `location_filter` | string | `"all"` | Локация: all, us, nonus |
| `company_filter` | string | `""` | Поиск по названию компании |
| `search` | string | `""` | Поиск по title + location + company |
| `states` | string | `""` | Штаты через запятую: NC,VA,SC |
| `include_remote_usa` | bool | `false` | Включить Remote-USA |
| `city` | string | `""` | Фильтр по городу |
| `geo_mode` | string | `"all"` | Гео-режим: all, nc_priority, local_only, neighbor_only, remote_usa |

**Примеры:**
```http
GET /jobs?states=NC,VA&include_remote_usa=true
GET /jobs?profile=fintech&role_filter=product
GET /jobs?search=senior+product&states=NC
```

**Response:**
```json
{
  "count": 150,
  "jobs": [
    {
      "company": "Brex",
      "title": "Senior Product Manager",
      "location": "San Francisco, CA",
      "url": "https://...",
      "updated_at": "2024-12-20T10:00:00Z",
      "ats": "greenhouse",
      "industry": "Fintech",
      "role_family": "product",
      "role_confidence": 1.0,
      "geo_bucket": "other",
      "geo_score": 0,
      "score": 30,
      "job_key": "https://...",
      "application_status": "New",
      "location_norm": {
        "raw": "San Francisco, CA",
        "city": "San Francisco",
        "state": "CA",
        "states": ["CA"],
        "remote": false,
        "remote_scope": null
      }
    }
  ]
}
```

---

### GET /job_status

Получить статусы заявок для профиля.

**Query параметры:**
| Параметр | Тип | По умолчанию |
|----------|-----|--------------|
| `profile` | string | `"all"` |

**Response:**
```json
{
  "count": 5,
  "statuses": {
    "https://job-url-1": "Applied",
    "https://job-url-2": "Rejected"
  }
}
```

---

### POST /job_status

Обновить статус заявки.

**Body:**
```json
{
  "profile": "all",
  "job_key": "https://boards.greenhouse.io/...",
  "status": "Applied"
}
```

**Допустимые статусы:** `New`, `Applied`, `Answered`, `Rejected`

**Response:**
```json
{
  "ok": true,
  "profile": "all",
  "job_key": "https://...",
  "status": "Applied"
}
```

---

### GET /companies

Список компаний профиля со статусом парсинга.

**Query параметры:**
| Параметр | Тип | По умолчанию |
|----------|-----|--------------|
| `profile` | string | `"all"` |

**Response:**
```json
{
  "count": 80,
  "companies": [
    {
      "company": "Brex",
      "industry": "Fintech",
      "ats": "greenhouse",
      "url": "https://boards.greenhouse.io/brex",
      "last_ok": true,
      "last_error": "",
      "last_checked": "2024-12-22T10:00:00Z"
    }
  ]
}
```

---

### GET /profiles/{name}

Компании из конкретного профиля.

**Response:**
```json
{
  "count": 15,
  "companies": [
    {
      "id": "brex",
      "name": "Brex",
      "ats": "greenhouse",
      "board_url": "https://boards.greenhouse.io/brex",
      "tags": ["fintech", "card"],
      "priority": 0,
      "hq_state": null,
      "region": "us"
    }
  ]
}
```

---

### GET /cache/info

Информация о кэше.

**Query параметры:**
| Параметр | Тип | По умолчанию |
|----------|-----|--------------|
| `cache_key` | string | `"all"` |

**Response:**
```json
{
  "exists": true,
  "valid": true,
  "last_updated": "2024-12-22T08:00:00Z",
  "age": "2 hours ago",
  "jobs_count": 450,
  "ttl_hours": 6
}
```

---

### POST /cache/refresh

Сбросить кэш (следующий запрос `/jobs` обновит данные).

**Query параметры:**
| Параметр | Тип | По умолчанию |
|----------|-----|--------------|
| `cache_key` | string | `"all"` |

**Response:**
```json
{
  "ok": true,
  "message": "Cache cleared for 'all'. Next /jobs request will refresh."
}
```

---

### DELETE /cache/clear

Очистить все кэши.

**Response:**
```json
{
  "ok": true,
  "message": "All caches cleared"
}
```

---

### GET /debug/location_stats

Статистика по локациям (для отладки).

**Query параметры:**
| Параметр | Тип | По умолчанию |
|----------|-----|--------------|
| `profile` | string | `"all"` |

**Response:**
```json
{
  "total_jobs": 450,
  "remote_usa_count": 85,
  "remote_global_count": 120,
  "jobs_with_states_count": 300,
  "top_20_states": [
    ["CA", 95],
    ["NY", 72],
    ["NC", 45]
  ]
}
```

---

## Коды ошибок

| Код | Описание |
|-----|----------|
| 200 | Успех |
| 422 | Ошибка валидации параметров |
| 500 | Внутренняя ошибка сервера |
