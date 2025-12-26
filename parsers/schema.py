# parsers/schema.py
"""
Job Data Contract - стандартная схема данных между парсерами и pipeline.

Все парсеры ДОЛЖНЫ возвращать список словарей с этими полями.
Pipeline и Cache ожидают именно эту структуру.
"""

from typing import TypedDict, Optional, List
from datetime import datetime


class RawJob(TypedDict, total=False):
    """
    Минимальная структура от парсера (RAW).
    Парсер заполняет что может, остальное — None.
    """
    # === REQUIRED (парсер ДОЛЖЕН заполнить) ===
    title: str              # Название позиции
    url: str                # Ссылка на вакансию
    
    # === EXPECTED (парсер ДОЛЖЕН попытаться заполнить) ===
    ats_job_id: str         # ID вакансии в ATS (для дедупликации)
    location: str           # Локация как строка (сырая)
    updated_at: str         # ISO datetime последнего обновления
    
    # === OPTIONAL (если есть в ATS) ===
    department: str         # Отдел/команда
    first_published: str    # Дата первой публикации
    description: str        # Текст описания (для AI классификации)
    

class EnrichedJob(RawJob):
    """
    Обогащённая структура после обработки в main.py (_fetch_for_company).
    Добавляются поля из нормализации и классификации.
    """
    # === ADDED BY PIPELINE ===
    id: str                 # Уникальный ID (hash от company+ats_job_id+url)
    company: str            # Название компании
    ats: str                # Тип ATS (greenhouse, lever, etc.)
    industry: str           # Индустрия компании
    
    # === LOCATION NORMALIZATION ===
    location_norm: dict     # {city, state, remote, remote_scope, ...}
    geo_bucket: str         # local / nc / neighbor / remote_usa / other
    geo_score: int          # 0-100 score для ранжирования
    
    # === ROLE CLASSIFICATION ===
    role_family: str        # product / tpm_program / project / other
    role_category: str      # primary / adjacent / excluded / unknown  
    role_id: str            # ID роли из roles.json
    role_confidence: float  # 0-1 уверенность классификации
    role_reason: str        # Почему такая классификация
    role_excluded: bool     # Исключена ли роль
    role_exclude_reason: str # Причина исключения
    
    # === COMPANY DATA ===
    company_data: dict      # {priority, hq_state, region, tags}


# === VALIDATION ===

REQUIRED_RAW_FIELDS = ["title", "url"]
EXPECTED_RAW_FIELDS = ["ats_job_id", "location", "updated_at"]
ENRICHED_FIELDS = ["id", "company", "ats", "location_norm", "role_family", "geo_bucket"]


def validate_raw_job(job: dict) -> tuple[bool, list[str]]:
    """
    Проверяет что парсер вернул минимально необходимые поля.
    Returns: (is_valid, list of missing/empty fields)
    """
    errors = []
    
    for field in REQUIRED_RAW_FIELDS:
        if not job.get(field):
            errors.append(f"Missing required: {field}")
    
    for field in EXPECTED_RAW_FIELDS:
        if not job.get(field):
            errors.append(f"Missing expected: {field}")
    
    return len([e for e in errors if "required" in e]) == 0, errors


def validate_enriched_job(job: dict) -> tuple[bool, list[str]]:
    """
    Проверяет что job полностью обогащён для pipeline.
    """
    errors = []
    
    for field in ENRICHED_FIELDS:
        if field not in job:
            errors.append(f"Missing enriched: {field}")
    
    return len(errors) == 0, errors


# === PARSER CONTRACT ===

"""
КОНТРАКТ ДЛЯ ПАРСЕРОВ:

1. Функция парсера должна называться: fetch_{ats_name}(company, board_url, **kwargs)
2. Возвращает: List[RawJob]
3. Обязательные поля в каждом job:
   - title: str
   - url: str
4. Ожидаемые поля (заполнить если есть):
   - ats_job_id: str (для дедупликации)
   - location: str (сырая строка)
   - updated_at: str (ISO format)
5. Опциональные поля:
   - department: str
   - first_published: str
   - description: str

ПРИМЕР:
```python
def fetch_newats(company: str, board_url: str) -> list[dict]:
    # ... fetch from API ...
    return [
        {
            "title": "Product Manager",
            "url": "https://...",
            "ats_job_id": "12345",
            "location": "San Francisco, CA",
            "updated_at": "2024-12-25T10:00:00Z",
            "department": "Product",
        },
        ...
    ]
```

После парсинга main.py автоматически добавит:
- id (unique hash)
- company, ats, industry
- location_norm, geo_bucket, geo_score
- role_family, role_category, role_id, etc.
- company_data
"""
