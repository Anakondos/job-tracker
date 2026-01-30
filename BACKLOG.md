# Job Tracker — Product Backlog

## 🎯 Current Focus Areas
1. Job Parsing & ATS Integration
2. CV Optimization & Tailoring
3. Cover Letter Generation
4. Job-Candidate Matching

---

## 📥 Parsing & ATS Integration

### High Priority
- [ ] **Workday parser fix** — иногда возвращает неправильную вакансию при onboard (поиск вместо прямого API)
- [ ] **Workday job list parsing** — парсить все вакансии компании, не только по одной
- [ ] **Ashby parser** — добавить поддержку Ashby ATS (API доступен)

### Medium Priority
- [ ] **iCIMS parser** — популярный ATS, нужен scraping
- [ ] **Jobvite parser** — добавить поддержку
- [ ] **Parallel parsing** — ускорить загрузку через ThreadPoolExecutor
- [ ] **Parser health dashboard** — показывать статус каждого парсера

### Low Priority
- [ ] **BambooHR parser**
- [ ] **JazzHR parser**
- [ ] **Universal fallback parser** — для неизвестных ATS через scraping

---

## 📄 CV Optimization

### High Priority
- [ ] **Full CV tailoring** — переписывать Summary под конкретную позицию
- [ ] **Bullet points adaptation** — подстраивать achievements под JD keywords
- [ ] **Skills section update** — добавлять релевантные skills из JD
- [ ] **Show CV diff** — показывать что именно изменилось в CV

### Medium Priority
- [ ] **Multiple CV versions** — хранить несколько версий (PM, TPM, Project)
- [ ] **Keyword injection tracking** — логировать какие keywords добавлены
- [ ] **CV scoring** — оценивать насколько CV соответствует JD
- [ ] **ATS-friendly formatting** — проверять что CV парсится ATS корректно

### Low Priority
- [ ] **PDF export** — генерировать PDF из DOCX
- [ ] **CV templates** — разные шаблоны под разные индустрии
- [ ] **LinkedIn sync** — синхронизация с LinkedIn профилем

---

## 💌 Cover Letter Generation

### High Priority
- [ ] **Improved CL quality** — более персонализированные письма
- [ ] **Company research integration** — использовать инфо о компании
- [ ] **Role-specific templates** — разные стили для PM/TPM/Project
- [ ] **CL preview in UI** — показывать полный текст перед сохранением

### Medium Priority
- [ ] **Multiple CL versions** — генерировать 2-3 варианта на выбор
- [ ] **Tone adjustment** — formal/casual/enthusiastic
- [ ] **Length control** — short/medium/long версии
- [ ] **Highlight selector** — выбирать какие achievements подчеркнуть

### Low Priority
- [ ] **Follow-up email templates** — шаблоны для follow-up
- [ ] **Thank you letter** — после интервью
- [ ] **Rejection response** — вежливый ответ на отказ

---

## 🎯 Job-Candidate Matching

### High Priority
- [ ] **Detailed keyword matching** — JD keywords vs CV keywords matrix
- [ ] **Skills gap analysis** — показывать какие skills отсутствуют
- [ ] **Experience level matching** — junior/mid/senior alignment
- [ ] **Location fit scoring** — учитывать релокацию, remote preferences

### Medium Priority
- [ ] **Company culture fit** — анализ culture signals из JD
- [ ] **Salary range estimation** — оценка зарплаты по позиции
- [ ] **Growth potential score** — оценка возможностей роста
- [ ] **Red flags detection** — выявление warning signs в JD

### Low Priority
- [ ] **Industry fit** — насколько опыт релевантен индустрии
- [ ] **Team size estimation** — оценка размера команды
- [ ] **Travel requirements** — парсинг требований к командировкам

---

## 🔄 Daemon Pipeline & Auto-Processing

### Phase 1: Deadline Parsing (Low effort)
- [ ] **Parse deadline from ATS APIs** — extract `close_date` (Greenhouse), `externalApplyDeadline` (Workday)
- [ ] **Add deadline column to UI** — show deadline if available
- [ ] **Sort by deadline** — urgent jobs first
- [ ] **Deadline alerts** — highlight jobs expiring in 3 days

### Phase 2: Auto JD Fetch (Medium effort)  
- [ ] **Fetch JD in daemon** — only for NEW + Primary/Adjacent + US/Remote jobs
- [ ] **Cache JD in pipeline.json** — avoid re-fetching
- [ ] **Parse deadline from JD text** — regex/AI extraction ("apply by", "deadline", etc.)
- [ ] **Extract salary from JD** — if mentioned

### Phase 3: Quick Matching (Medium effort)
- [ ] **Keyword matching** — JD keywords vs profile keywords (no AI)
- [ ] **Title matching score** — how close to target roles
- [ ] **Location score** — NC > Neighbor > Remote > Other
- [ ] **Combined relevance score** — weighted sum of above
- [ ] **Auto-filter by score** — show "Recommended" badge for score > 80%

### Phase 4: AI Matching (High effort, on-demand)
- [ ] **AI matching only for Recommended** — limit API calls
- [ ] **Skills gap analysis** — what's missing vs JD requirements  
- [ ] **Experience alignment** — years/level match
- [ ] **Store AI analysis** — cache in job metadata
- [ ] **Matching explanation** — why this job is/isn't a good fit

### Phase 5: Auto-Prepare Documents (High effort, on-demand)
- [ ] **Auto-prepare for high-match jobs** — score > 90%
- [ ] **Queue system** — prepare in background
- [ ] **Notification** — "3 jobs ready to apply"
- [ ] **One-click apply** — open job + docs ready

### Optimization Rules
- Only process: NEW status + Primary/Adjacent roles + US/NC/Remote
- Rate limit: max 50 JD fetches per daemon cycle
- AI calls: only for "Selected" or "Recommended" jobs
- Cache everything: JD, analysis, matching scores

---

## 🖥️ UI/UX Improvements

### High Priority
- [ ] **Keywords display in existing files modal** — показывать добавленные keywords ✅ DONE
- [ ] **Added date column** — когда вакансия добавлена в pipeline ✅ DONE
- [ ] **Bulk status change** — менять статус для нескольких вакансий

### Medium Priority
- [ ] **Dark/Light theme toggle**
- [ ] **Keyboard shortcuts** — быстрая навигация
- [ ] **Job comparison view** — сравнивать 2-3 вакансии side-by-side
- [ ] **Application timeline** — визуализация воронки

### Low Priority
- [ ] **Mobile responsive** — адаптация для телефона
- [ ] **Export to spreadsheet** — экспорт в Excel/CSV
- [ ] **Calendar integration** — синхронизация интервью с календарём

---

## 🤖 Form Filling & Automation

### High Priority
- [ ] **Workday form filler** — поддержка Workday forms
- [ ] **Lever form filler** — поддержка Lever forms
- [ ] **SmartRecruiters form filler** — поддержка SR forms

### Medium Priority
- [ ] **Skyvern integration** — использовать Skyvern для сложных форм
- [ ] **Auto-save answers** — сохранять ответы на вопросы для reuse
- [ ] **Question bank** — база ответов на типовые вопросы

### Low Priority
- [ ] **CAPTCHA handling** — интеграция с CAPTCHA solvers
- [ ] **2FA support** — поддержка двухфакторной авторизации
- [ ] **Headless mode** — фоновое заполнение форм

---

## 📊 Analytics & Reporting

### Medium Priority
- [ ] **Application funnel metrics** — конверсия по этапам
- [ ] **Response rate tracking** — % ответов от компаний
- [ ] **Time-to-response** — среднее время ответа
- [ ] **Best time to apply** — анализ лучшего времени для отклика

### Low Priority
- [ ] **Weekly digest** — еженедельный отчёт
- [ ] **Goal tracking** — трекинг целей (X откликов в неделю)
- [ ] **Salary negotiation data** — данные для переговоров

---

## 🔧 Technical Debt

- [ ] **Standardize API responses** — единый формат ответов
- [ ] **Error handling improvement** — better error messages
- [ ] **Logging system** — structured logging
- [ ] **Unit tests** — покрытие тестами
- [ ] **API documentation** — Swagger/OpenAPI docs
- [ ] **Config file** — вынести настройки в config.yaml

---

## ✅ Completed

- [x] Workday API integration for job analysis (Jan 29, 2026)
- [x] Added date column in jobs table (Jan 29, 2026)
- [x] Keywords display in existing files modal (Jan 29, 2026)
- [x] metadata.json saving with keywords (Jan 29, 2026)
- [x] V6 form filler for Greenhouse (Jan 29, 2026)
- [x] Multi-machine sync with lock files
- [x] AI-powered job analysis before adding to pipeline

---

*Last updated: January 29, 2026*
