# V7 Smart Form Filler - Design Document

## Проблема V6
- Hardcoded selectors (#first_name, #school--0)
- Hardcoded answer patterns  
- Не адаптируется к новым формам
- Не учится на ошибках

## Решение V7: AI-Assisted Universal Filler

### Уровни заполнения:

```
Level 1: AUTO (90% полей)
├── Profile match: "First Name" → profile.first_name
├── Learned DB: "Have you worked at X?" → saved answer
└── Pattern match: "sponsorship" → "No"

Level 2: AI ASSIST (9% полей)  
├── Field unclear → AI determines answer from context
├── Options unclear → AI picks best match
└── Save decision to learned DB

Level 3: AI CONTROL (1% полей)
├── Fill completely failed
├── AI takes screenshot, analyzes
├── AI fills manually, explains why
└── Saves solution for future
```

### Ключевые компоненты:

#### 1. Universal Field Scanner
```python
def scan_form(frame) -> List[Field]:
    """Scan ANY form, detect fields universally"""
    fields = []
    
    # Find by common patterns (not hardcoded IDs)
    for el in frame.query_selector_all('input, select, textarea, [role="combobox"]'):
        field = Field(
            element=el,
            label=find_label(el),  # <label>, aria-label, placeholder
            field_type=detect_type(el),  # text, dropdown, file, checkbox
            options=get_options(el) if is_dropdown(el) else None,
            position=get_visual_position(el),
        )
        fields.append(field)
    
    # Sort by visual position
    return sorted(fields, key=lambda f: (f.position.y, f.position.x))
```

#### 2. Smart Answer Matcher
```python
def find_answer(field: Field, profile: Profile, learned_db: dict) -> Answer:
    """Find answer using cascade"""
    
    label = field.label.lower()
    
    # 1. Exact match in learned DB
    if label in learned_db:
        return Answer(learned_db[label], source="learned")
    
    # 2. Profile field match
    profile_match = match_profile_field(label, profile)
    if profile_match:
        return Answer(profile_match, source="profile")
    
    # 3. Pattern match (defaults)
    pattern_match = match_pattern(label)
    if pattern_match:
        return Answer(pattern_match, source="pattern")
    
    # 4. ASK AI
    ai_answer = ask_ai(field, profile)
    return Answer(ai_answer, source="ai")
```

#### 3. AI-Assisted Fill
```python
async def fill_with_ai_fallback(field, answer):
    """Try fill, AI takes over on failure"""
    
    try:
        success = fill_field(field, answer)
        if success:
            return True
    except Exception as e:
        pass
    
    # AI takes control
    screenshot = take_screenshot()
    
    ai_solution = await claude.analyze(
        f"Fill failed for '{field.label}'. Error: {e}. "
        f"Options: {field.options}. What to do?",
        images=[screenshot]
    )
    
    # Execute AI solution
    execute_steps(ai_solution.steps)
    
    # Save for future
    learned_db.save(field.label, ai_solution)
```

### Learned Database:
```json
{
  "answers": {
    "have you previously been employed by coinbase": "No",
    "are you legally authorized": "Yes"
  },
  "field_methods": {
    "location": {"type": "autocomplete", "wait_api": 2.0},
    "school": {"type": "searchable", "fallback": "0 - Other"}
  },
  "ats_selectors": {
    "greenhouse": {"first_name": "#first_name"},
    "lever": {"first_name": "input[name='name']"}
  }
}
```

## Что нужно от AI:

1. **Answer determination**: "What answer for 'Are you a government official?'"
2. **Option matching**: "Best option for 'sponsorship: No' from [list]"
3. **Debug failed fill**: "Screenshot + error → how to fix?"
4. **Learn patterns**: "This worked → save for similar fields"
