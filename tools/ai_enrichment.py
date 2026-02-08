#!/usr/bin/env python3
"""
AI Enrichment Tool — заполнение пустых tags/industry/hq_state через Claude API

Для enabled-компаний с пустыми метаданными:
- industry: IT, Fintech, Healthcare, Consulting, Gaming, Retail, etc.
- tags: из словаря tag_mappings (security, devtools, fintech, saas, etc.)
- hq_state: US state abbreviation или null

Использование:
    python tools/ai_enrichment.py --dry-run    # предпросмотр
    python tools/ai_enrichment.py              # применить
    python tools/ai_enrichment.py --list       # показать компании без метаданных
"""

import json
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

COMPANIES_FILE = PROJECT_ROOT / "data" / "companies.json"

# Загружаем .env (override=True нужен чтобы перезаписать пустые значения)
load_dotenv(PROJECT_ROOT / ".env", override=True)

# Допустимые теги (из company_storage.py tag_mappings)
VALID_TAGS = [
    # Security
    "security", "identity", "compliance", "cybersecurity",
    # DevTools & Infra
    "devtools", "devops", "cloud", "infra", "api", "ci",
    "hosting", "cdn", "edge", "storage", "platform", "database",
    "incident", "feature-flags", "developer-tools", "observability",
    # AI & Data
    "ai", "data", "analytics", "lakehouse", "research", "ml",
    # Fintech
    "fintech", "payments", "banking", "neobank", "investment",
    "card", "cards", "crypto", "exchange", "trading", "finance",
    "loans", "roboadvisor", "bnpl", "bank", "payroll",
    # Enterprise SaaS
    "saas", "crm", "hr", "productivity", "collaboration",
    "sales", "marketing", "support", "automation", "internal-tools",
    "pm-tools", "nocode", "lowcode", "field-service",
    # Other
    "consumer", "video", "hardware", "ecommerce", "edtech",
    "social", "community", "marketplace", "travel", "streaming",
    "retail", "design", "language", "communications", "networking",
    "bigtech", "gaming", "healthtech", "biotech", "consulting",
    "construction-tech", "adtech", "blockchain", "iot", "global",
    "workplace", "software", "enterprise",
]

# Допустимые industries
VALID_INDUSTRIES = [
    "Fintech", "IT", "Healthcare", "Consulting", "Gaming",
    "Retail", "Banking", "Security", "AI/ML", "Data",
    "DevTools", "Enterprise SaaS", "Cloud", "E-commerce",
    "Social", "EdTech", "Hardware", "Biotech", "Telecommunications",
    "Manufacturing", "Other",
]


def load_companies() -> list:
    with open(COMPANIES_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_companies(companies: list):
    with open(COMPANIES_FILE, "w", encoding="utf-8") as f:
        json.dump(companies, f, indent=2, ensure_ascii=False)
    print(f"  ✅ Сохранено {len(companies)} компаний")


def get_companies_needing_enrichment(companies: list) -> list:
    """Находим enabled-компании с пустыми tags ИЛИ industry"""
    needs = []
    for c in companies:
        if not c.get("enabled", True):
            continue
        missing_tags = not c.get("tags")
        missing_industry = not c.get("industry")
        if missing_tags or missing_industry:
            needs.append(c)
    return needs


def call_claude_api(prompt: str, max_tokens: int = 4000) -> str | None:
    """Вызов Claude API (паттерн из ats_parser_generator.py)"""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        print("❌ ANTHROPIC_API_KEY not set in .env")
        return None

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}]
        )
        return message.content[0].text
    except ImportError:
        print("❌ anthropic package not installed. Run: pip install anthropic")
        return None
    except Exception as e:
        print(f"❌ Claude API error: {e}")
        return None


def build_enrichment_prompt(batch: list) -> str:
    """Формируем промпт для batch-обогащения"""
    companies_text = ""
    for c in batch:
        companies_text += f"- id: {c.get('id')}, name: {c.get('name')}, ats: {c.get('ats')}, board_url: {c.get('board_url')}\n"

    prompt = f"""Я даю тебе список IT-компаний с их карьерными страницами.
Для каждой компании определи:
1. industry — одно из: {', '.join(VALID_INDUSTRIES)}
2. tags — список из 1-4 тегов из допустимых: {', '.join(sorted(set(VALID_TAGS)))}
3. hq_state — штат штаб-квартиры (2 буквы, например "CA", "NY", "NC") или null если не в США или неизвестно

Компании:
{companies_text}

Ответь СТРОГО в формате JSON-массива, без markdown-блоков, без комментариев:
[
  {{"id": "company_id", "industry": "...", "tags": ["tag1", "tag2"], "hq_state": "CA"}},
  ...
]

ВАЖНО:
- Используй ТОЛЬКО теги из списка выше
- Используй ТОЛЬКО industries из списка выше
- Если не уверен — ставь industry: "Other", tags: ["software"], hq_state: null
- Верни ответ для КАЖДОЙ компании из списка
"""
    return prompt


def parse_enrichment_response(response: str) -> list:
    """Парсим JSON ответ от Claude"""
    # Убираем markdown-обёртки если есть
    text = response.strip()
    if text.startswith("```"):
        # Убираем первую строку (```json) и последнюю (```)
        lines = text.split("\n")
        text = "\n".join(lines[1:-1])

    try:
        result = json.loads(text)
        if isinstance(result, list):
            return result
    except json.JSONDecodeError as e:
        print(f"  ⚠️ Ошибка парсинга JSON: {e}")
        print(f"  Ответ: {text[:200]}...")
    return []


def apply_enrichment(companies: list, enrichments: list, dry_run: bool) -> int:
    """Применяем обогащение к компаниям"""
    # Индексируем enrichments по id
    enrich_map = {e["id"]: e for e in enrichments if "id" in e}
    changes = 0

    for c in companies:
        cid = c.get("id")
        if cid not in enrich_map:
            continue

        e = enrich_map[cid]
        changed = False

        # Заполняем industry если пусто
        if not c.get("industry") and e.get("industry"):
            industry = e["industry"]
            if industry in VALID_INDUSTRIES:
                print(f"    [{cid}] industry → {industry}")
                if not dry_run:
                    c["industry"] = industry
                changed = True

        # Заполняем tags если пусто
        if not c.get("tags") and e.get("tags"):
            tags = [t for t in e["tags"] if t in VALID_TAGS]
            if tags:
                print(f"    [{cid}] tags → {tags}")
                if not dry_run:
                    c["tags"] = tags
                changed = True

        # Заполняем hq_state если пусто
        if not c.get("hq_state") and e.get("hq_state"):
            hq = e["hq_state"]
            if isinstance(hq, str) and len(hq) == 2:
                print(f"    [{cid}] hq_state → {hq}")
                if not dry_run:
                    c["hq_state"] = hq
                changed = True

        if changed:
            changes += 1

    return changes


def main():
    dry_run = "--dry-run" in sys.argv
    list_only = "--list" in sys.argv

    mode = "DRY RUN" if dry_run else "APPLY"
    print(f"\n🤖 AI Enrichment Tool [{mode}]")
    print("=" * 50)

    companies = load_companies()
    needs_enrichment = get_companies_needing_enrichment(companies)

    print(f"📂 Всего компаний: {len(companies)}")
    print(f"🔍 Нуждаются в обогащении: {len(needs_enrichment)}")

    if list_only:
        print("\nКомпании без метаданных:")
        for c in needs_enrichment:
            tags = c.get("tags", [])
            ind = c.get("industry", "")
            hq = c.get("hq_state", "")
            print(f"  [{c['id']}] industry={ind or '❌'} tags={tags or '❌'} hq={hq or '❌'}")
        return

    if not needs_enrichment:
        print("✅ Все компании уже обогащены!")
        return

    # Batch по 10 компаний
    batch_size = 10
    total_changes = 0

    for i in range(0, len(needs_enrichment), batch_size):
        batch = needs_enrichment[i:i + batch_size]
        batch_num = i // batch_size + 1
        total_batches = (len(needs_enrichment) + batch_size - 1) // batch_size

        print(f"\n--- Batch {batch_num}/{total_batches} ({len(batch)} компаний) ---")
        for c in batch:
            print(f"  • {c['name']} ({c['ats']})")

        if dry_run:
            print("  [DRY RUN] Пропускаем API вызов")
            continue

        # Вызов Claude
        prompt = build_enrichment_prompt(batch)
        print("  🔄 Запрос к Claude API...")
        response = call_claude_api(prompt)

        if not response:
            print("  ❌ Нет ответа от API, пропускаем batch")
            continue

        enrichments = parse_enrichment_response(response)
        if not enrichments:
            print("  ❌ Не удалось распарсить ответ")
            continue

        print(f"  ✅ Получено {len(enrichments)} результатов")
        changes = apply_enrichment(companies, enrichments, dry_run=False)
        total_changes += changes

    print(f"\n{'=' * 50}")
    print(f"📝 Всего обогащено компаний: {total_changes}")

    if not dry_run and total_changes > 0:
        save_companies(companies)
    elif dry_run:
        print("⚠️ DRY RUN — изменения НЕ сохранены")

    # Итоговая статистика
    companies = load_companies()
    enabled = [c for c in companies if c.get("enabled", True)]
    no_tags = sum(1 for c in enabled if not c.get("tags"))
    no_industry = sum(1 for c in enabled if not c.get("industry"))
    print(f"\n📊 Итого enabled компаний без тегов: {no_tags}")
    print(f"📊 Итого enabled компаний без industry: {no_industry}")


if __name__ == "__main__":
    main()
