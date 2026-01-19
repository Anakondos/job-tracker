# Job Tracker — Changelog / Журнал изменений

## Правила работы с кодом

1. **`static/index.html`** — ЗАПРЕЩЕНО менять без одобрения пользователя
2. **Новый функционал** — только в `static/js/*.js`, `static/css/*.css`
3. **Перед изменением** — показать план и получить одобрение
4. **После изменения** — записать в этот журнал

---

## 2026-01-19

### [10:45] Fix daemon company filter
- **Что сделано:** Daemon теперь фильтрует по `enabled: True` вместо `disabled != True`
- **Файл:** `main.py` (строка 412)
- **Коммит:** `3682d2d`
- **Результат:** 79 компаний вместо 93 (14 disabled пропускаются)
- **Статус:** ✅ Работает

### [10:28] Fix /stats endpoint - use pipeline data
- **Что сделано:** `/stats` теперь читает из pipeline (`jobs_new.json`) вместо старого cache
- **Файл:** `main.py` (строки 1558-1585)
- **Коммит:** `32b1503`
- **Результат:** `is_stale: false`, warning больше не показывается
- **Статус:** ✅ Работает

### [10:25] Microservices Architecture Init
- **Что сделано:** Созданы папки `static/js/` и `static/css/` для новых модулей
- **Файлы:** `static/js/.gitkeep`, `static/css/.gitkeep`
- **Коммит:** `725d8ef`
- **Статус:** ✅ Работает
- **Примечание:** Старый код в index.html заморожен, новый функционал только в отдельных файлах

### [10:16] Revert stale warning removal
- **Что сделано:** Откат удаления stale warning (ломало JS)
- **Коммит:** `fce771c`
- **Урок:** Перед удалением функции проверять все места вызова через grep

### [10:07] Add refresh log panel
- **Что сделано:** Добавлен выпадающий лог прогресса daemon (клик на статус)
- **Файлы:** `static/index.html`, `main.py`
- **Коммит:** `c157a38`
- **Статус:** ✅ Работает

### [10:00] Fix daemon error
- **Что сделано:** Добавлена функция `update_company_status` в `company_storage.py`
- **Коммит:** `8ca7ac3`

### [09:55] Fix JS errors
- **Что сделано:** Optional chaining для удалённых filter элементов
- **Коммит:** `337b54d`

### [09:50] Fix defaults
- **Что сделано:** Default filter = "To Apply" + "All time" (избегаем пустого экрана)
- **Коммит:** `f17dfe8`

### [09:45] Restore Quick Filters
- **Что сделано:** Восстановлен UI с Quick Filters из коммита 07de342
- **Коммит:** `8baa23a`

---

## Структура проекта

```
static/
├── index.html              # [LOCKED] Главный UI (не трогать!)
├── js/                     # [NEW] Микросервисы JS
│   └── .gitkeep
├── css/                    # [NEW] Микросервисы CSS  
│   └── .gitkeep
└── favicon.svg
```

## Текущее состояние

- **UI:** Quick Filters работают (To Apply, New this week, Applied, All)
- **Daemon:** Автообновление работает, лог по клику на статус
- **Data:** Pipeline 1077 jobs, 93 компании
