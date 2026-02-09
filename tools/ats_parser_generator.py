#!/usr/bin/env python3
"""
ATS Parser Generator - автоматически создаёт парсеры для новых ATS
используя Claude API для анализа собранных данных.

Может работать автономно без участия пользователя.

Использование:
    python tools/ats_parser_generator.py analyze phenom
    python tools/ats_parser_generator.py generate phenom
    python tools/ats_parser_generator.py test phenom
    python tools/ats_parser_generator.py auto  # Полный цикл для всех pending ATS
"""

import json
import os
import sys
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

DATA_FILE = PROJECT_ROOT / "data" / "unsupported_ats.json"
PARSERS_DIR = PROJECT_ROOT / "parsers"
MAIN_PY = PROJECT_ROOT / "main.py"

# Claude API settings
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")


def load_unsupported_ats() -> Dict:
    """Load the unsupported ATS data file."""
    if DATA_FILE.exists():
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    return {"ats_systems": {}}


def save_unsupported_ats(data: Dict):
    """Save the unsupported ATS data file."""
    data["_updated_at"] = datetime.now().isoformat()
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def get_ats_data(ats_name: str) -> Optional[Dict]:
    """Get collected data for specific ATS."""
    data = load_unsupported_ats()
    ats_key = ats_name.lower().replace(" ", "_")
    return data.get("ats_systems", {}).get(ats_key)


def build_prompt_for_parser(ats_name: str, ats_data: Dict) -> str:
    """Build a prompt for Claude to generate a parser."""

    # Format endpoints
    endpoints_text = ""
    for ep in ats_data.get("api_endpoints", [])[:15]:
        if isinstance(ep, dict):
            endpoints_text += f"  - {ep.get('method', 'GET')} {ep.get('url', '')}\n"
        else:
            endpoints_text += f"  - {ep}\n"

    # Format sample responses
    samples_text = ""
    for sample in ats_data.get("sample_responses", [])[:5]:
        if isinstance(sample, dict):
            samples_text += f"URL: {sample.get('url', 'N/A')}\n"
            samples_text += f"Sample: {sample.get('sample', 'N/A')[:500]}\n"
            samples_text += f"Has jobs array: {sample.get('has_jobs_array', False)}\n\n"

    # Companies using this ATS
    companies = ats_data.get("companies_using", [])[:5]
    companies_text = "\n".join(f"  - {c}" for c in companies)

    prompt = f"""Создай Python парсер для ATS системы "{ats_name}".

## Собранные данные:

### Компании использующие этот ATS:
{companies_text}

### Обнаруженные API endpoints:
{endpoints_text}

### Примеры JSON ответов:
{samples_text}

## Требования к парсеру:

1. Файл должен называться `parsers/{ats_name.lower()}.py`

2. Основная функция:
```python
def fetch_{ats_name.lower()}_jobs(company: str, base_url: str) -> List[Dict]:
```

3. Возвращаемый формат (обязательные поля):
```python
{{
    "company": company,
    "ats": "{ats_name.lower()}",
    "ats_job_id": str,      # уникальный ID вакансии
    "title": str,           # название позиции
    "location": str,        # локация
    "department": str,      # отдел (если есть)
    "url": str,             # URL вакансии
    "first_published": str, # дата публикации
    "updated_at": str,      # дата обновления
}}
```

4. Парсер должен:
   - Извлекать slug компании из base_url
   - Делать запрос к API
   - Парсить ответ и возвращать список вакансий
   - Обрабатывать ошибки gracefully

5. Добавь тестовый блок `if __name__ == "__main__":`

## Важно:
- Проанализируй endpoints и samples чтобы понять структуру API
- Если API требует POST запрос - используй правильный формат
- Если есть пагинация - учти её
- Код должен быть готов к использованию

Верни ТОЛЬКО код парсера, без объяснений.
"""

    return prompt


def call_claude_api(prompt: str, max_tokens: int = 4000) -> Optional[str]:
    """Call Claude API to generate parser code."""
    if not ANTHROPIC_API_KEY:
        print("❌ ANTHROPIC_API_KEY not set")
        return None

    try:
        import anthropic

        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=max_tokens,
            messages=[
                {"role": "user", "content": prompt}
            ]
        )

        return message.content[0].text

    except ImportError:
        print("❌ anthropic package not installed. Run: pip install anthropic")
        return None
    except Exception as e:
        print(f"❌ Claude API error: {e}")
        return None


def extract_code_from_response(response: str) -> str:
    """Extract Python code from Claude's response."""
    # If response contains code blocks, extract them
    if "```python" in response:
        start = response.find("```python") + len("```python")
        end = response.find("```", start)
        if end > start:
            return response[start:end].strip()

    # If response contains generic code blocks
    if "```" in response:
        start = response.find("```") + 3
        # Skip language identifier if present
        newline = response.find("\n", start)
        if newline > start and newline - start < 20:
            start = newline + 1
        end = response.find("```", start)
        if end > start:
            return response[start:end].strip()

    # Return as-is (might already be clean code)
    return response.strip()


def test_parser(ats_name: str) -> Dict[str, Any]:
    """Test the generated parser."""
    result = {
        "ok": False,
        "jobs_count": 0,
        "error": None,
        "sample_jobs": [],
    }

    ats_key = ats_name.lower().replace(" ", "_")
    parser_path = PARSERS_DIR / f"{ats_key}.py"

    if not parser_path.exists():
        result["error"] = f"Parser not found: {parser_path}"
        return result

    try:
        # Run the parser as a subprocess
        proc = subprocess.run(
            [sys.executable, str(parser_path)],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=str(PROJECT_ROOT),
        )

        output = proc.stdout + proc.stderr

        # Check for errors
        if proc.returncode != 0:
            result["error"] = f"Parser failed: {output[:500]}"
            return result

        # Try to parse output for job count
        if "Found" in output and "jobs" in output:
            import re
            match = re.search(r"Found (\d+) jobs", output)
            if match:
                result["jobs_count"] = int(match.group(1))

        result["ok"] = True
        result["output"] = output[:1000]

    except subprocess.TimeoutExpired:
        result["error"] = "Parser timed out after 60s"
    except Exception as e:
        result["error"] = str(e)

    return result


def update_parser_status(ats_name: str, status: str, notes: str = ""):
    """Update the status of a parser in the data file."""
    data = load_unsupported_ats()
    ats_key = ats_name.lower().replace(" ", "_")

    if ats_key in data.get("ats_systems", {}):
        data["ats_systems"][ats_key]["parser_status"] = status
        data["ats_systems"][ats_key]["last_updated"] = datetime.now().isoformat()
        if notes:
            data["ats_systems"][ats_key]["notes"] = notes
        save_unsupported_ats(data)


def register_parser_in_main(ats_name: str) -> Dict[str, Any]:
    """
    Automatically register a new parser in main.py ATS_PARSERS dict.

    This modifies main.py to:
    1. Add import statement for the new parser
    2. Add entry to ATS_PARSERS dict
    """
    result = {
        "ok": False,
        "error": None,
        "changes": [],
    }

    ats_key = ats_name.lower().replace(" ", "_")
    parser_path = PARSERS_DIR / f"{ats_key}.py"

    # Check parser exists
    if not parser_path.exists():
        result["error"] = f"Parser file not found: {parser_path}"
        return result

    # Read main.py
    with open(MAIN_PY, "r") as f:
        content = f.read()

    # Check if already registered
    if f'"{ats_key}"' in content and f"fetch_{ats_key}" in content:
        result["ok"] = True
        result["changes"].append("Already registered")
        return result

    import re

    # 1. Add import statement after other parser imports (at the top of file)
    import_line = f"from parsers.{ats_key} import fetch_{ats_key}_jobs"

    # Check if import already exists
    if import_line in content:
        result["changes"].append(f"Import already exists")
    else:
        # Find parser imports block at the top (before ATS_PARSERS)
        # Look for the last "from parsers.X import Y" line that's before "from ats_detector"
        lines = content.split('\n')
        insert_line_idx = None

        for i, line in enumerate(lines):
            if line.startswith("from parsers.") and "import" in line:
                insert_line_idx = i
            # Stop when we hit non-parser imports
            if line.startswith("from ats_detector") or line.startswith("from company_storage"):
                break

        if insert_line_idx is not None:
            lines.insert(insert_line_idx + 1, import_line)
            content = '\n'.join(lines)
            result["changes"].append(f"Added import: {import_line}")

    # 2. Add to ATS_PARSERS dict
    # Find the ATS_PARSERS dict
    ats_parsers_pattern = r'(ATS_PARSERS = \{[^}]+)'
    match = re.search(ats_parsers_pattern, content)

    if match:
        ats_dict_content = match.group(1)

        # Check if already in dict
        if f'"{ats_key}"' not in ats_dict_content:
            # Find position before closing brace
            # Add new entry
            new_entry = f'    "{ats_key}": fetch_{ats_key}_jobs,'

            # Insert before the closing brace
            # Find the end of the dict (last entry)
            last_entry_pattern = r'(ATS_PARSERS = \{.*?)(,\s*\n\})'

            # Simpler approach: find "}" after ATS_PARSERS and insert before it
            dict_start = content.find("ATS_PARSERS = {")
            if dict_start != -1:
                # Find the closing brace
                brace_count = 0
                for i, char in enumerate(content[dict_start:]):
                    if char == '{':
                        brace_count += 1
                    elif char == '}':
                        brace_count -= 1
                        if brace_count == 0:
                            # Found closing brace position
                            insert_pos = dict_start + i
                            # Check if there's already a newline before }
                            # Look backwards for the last non-whitespace
                            before = content[:insert_pos].rstrip()
                            # Add newline if needed
                            if before.endswith(','):
                                # Previous line ends with comma, add new entry on new line
                                content = before + f'\n    "{ats_key}": fetch_{ats_key}_jobs,' + content[insert_pos:]
                            else:
                                # Add comma to previous line and new entry
                                content = before + f',\n    "{ats_key}": fetch_{ats_key}_jobs,' + content[insert_pos:]
                            result["changes"].append(f'Added to ATS_PARSERS: "{ats_key}"')
                            break

    # Write back
    if result["changes"]:
        with open(MAIN_PY, "w") as f:
            f.write(content)
        result["ok"] = True
        print(f"✅ Registered {ats_key} in main.py:")
        for change in result["changes"]:
            print(f"   - {change}")
    else:
        result["error"] = "Could not find insertion points in main.py"

    return result


def unregister_parser_from_main(ats_name: str) -> Dict[str, Any]:
    """Remove a parser from main.py (for rollback)."""
    result = {"ok": False, "error": None}

    ats_key = ats_name.lower().replace(" ", "_")

    with open(MAIN_PY, "r") as f:
        content = f.read()

    import re

    # Remove import line
    import_pattern = rf'\nfrom parsers\.{ats_key} import fetch_{ats_key}_jobs'
    content = re.sub(import_pattern, '', content)

    # Remove from ATS_PARSERS
    entry_pattern = rf'\s*"{ats_key}":\s*fetch_{ats_key}_jobs,?\n?'
    content = re.sub(entry_pattern, '', content)

    with open(MAIN_PY, "w") as f:
        f.write(content)

    result["ok"] = True
    print(f"✅ Unregistered {ats_key} from main.py")
    return result


def generate_parser(ats_name: str, auto_save: bool = False) -> Dict[str, Any]:
    """Generate a parser for the specified ATS using Claude."""
    result = {
        "ok": False,
        "ats_name": ats_name,
        "parser_path": None,
        "error": None,
    }

    # Get collected data
    ats_data = get_ats_data(ats_name)
    if not ats_data:
        result["error"] = f"No data found for ATS: {ats_name}"
        return result

    print(f"\n🤖 Generating parser for: {ats_name}")
    print(f"   Companies: {len(ats_data.get('companies_using', []))}")
    print(f"   Endpoints: {len(ats_data.get('api_endpoints', []))}")
    print(f"   Samples: {len(ats_data.get('sample_responses', []))}")

    # Build prompt
    prompt = build_prompt_for_parser(ats_name, ats_data)

    # Call Claude
    print("\n📡 Calling Claude API...")
    response = call_claude_api(prompt)

    if not response:
        result["error"] = "Failed to get response from Claude"
        return result

    # Extract code
    code = extract_code_from_response(response)

    if not code or len(code) < 100:
        result["error"] = "Generated code is too short or empty"
        return result

    # Save parser
    ats_key = ats_name.lower().replace(" ", "_")
    parser_path = PARSERS_DIR / f"{ats_key}.py"

    print(f"\n📝 Generated parser ({len(code)} chars)")
    print("-" * 50)
    print(code[:800] + "..." if len(code) > 800 else code)
    print("-" * 50)

    if auto_save:
        with open(parser_path, "w") as f:
            f.write(code)
        print(f"\n✅ Saved to: {parser_path}")
        result["parser_path"] = str(parser_path)
        result["ok"] = True

        # Update status
        update_parser_status(ats_name, "generated")
    else:
        print(f"\n(Use --save to save to {parser_path})")

    return result


def auto_generate_all():
    """Automatically generate parsers for all pending ATS systems."""
    data = load_unsupported_ats()

    pending = []
    for ats_key, ats_data in data.get("ats_systems", {}).items():
        status = ats_data.get("parser_status", "not_started")
        if status == "not_started":
            # Check if we have enough data
            if len(ats_data.get("api_endpoints", [])) >= 3:
                pending.append(ats_key)

    if not pending:
        print("✅ No pending ATS systems with sufficient data")
        return

    print(f"\n🔄 Found {len(pending)} ATS systems to process:")
    for ats in pending:
        print(f"   - {ats}")

    results = {"success": [], "failed": []}

    for ats_name in pending:
        print(f"\n{'='*50}")

        # Generate parser
        gen_result = generate_parser(ats_name, auto_save=True)

        if not gen_result["ok"]:
            print(f"❌ Generation failed: {gen_result['error']}")
            results["failed"].append({"ats": ats_name, "error": gen_result["error"]})
            update_parser_status(ats_name, "generation_failed", gen_result["error"])
            continue

        # Test parser
        print(f"\n🧪 Testing parser...")
        test_result = test_parser(ats_name)

        if test_result["ok"] and test_result["jobs_count"] > 0:
            print(f"✅ Parser works! Found {test_result['jobs_count']} jobs")

            # Register in main.py automatically
            print(f"\n📝 Registering parser in main.py...")
            reg_result = register_parser_in_main(ats_name)

            if reg_result["ok"]:
                results["success"].append({"ats": ats_name, "jobs": test_result["jobs_count"], "registered": True})
                update_parser_status(ats_name, "completed", f"Found {test_result['jobs_count']} jobs, registered in main.py")
            else:
                results["success"].append({"ats": ats_name, "jobs": test_result["jobs_count"], "registered": False})
                update_parser_status(ats_name, "tested_ok", f"Found {test_result['jobs_count']} jobs, registration failed: {reg_result['error']}")
        elif test_result["ok"] and test_result["jobs_count"] == 0:
            print(f"⚠️ Parser runs but found 0 jobs - needs manual review")
            results["failed"].append({"ats": ats_name, "error": "Parser works but found 0 jobs"})
            update_parser_status(ats_name, "needs_review", "Parser runs but found 0 jobs")
        else:
            print(f"⚠️ Parser generated but test failed: {test_result['error']}")
            results["failed"].append({"ats": ats_name, "error": test_result["error"]})
            update_parser_status(ats_name, "test_failed", test_result["error"])

    # Summary
    print(f"\n{'='*50}")
    print(f"📊 Summary:")
    print(f"   ✅ Success: {len(results['success'])}")
    print(f"   ❌ Failed: {len(results['failed'])}")

    return results


def main():
    if len(sys.argv) < 2:
        print("""
ATS Parser Generator
====================

Автоматически создаёт парсеры для новых ATS используя Claude API.

Usage:
  python tools/ats_parser_generator.py analyze <ats_name>   - Показать собранные данные
  python tools/ats_parser_generator.py generate <ats_name>  - Сгенерировать парсер
  python tools/ats_parser_generator.py generate <ats_name> --save  - Сгенерировать и сохранить
  python tools/ats_parser_generator.py test <ats_name>      - Протестировать парсер
  python tools/ats_parser_generator.py register <ats_name>  - Зарегистрировать парсер в main.py
  python tools/ats_parser_generator.py unregister <ats_name> - Удалить парсер из main.py
  python tools/ats_parser_generator.py auto                 - Полный цикл: генерация → тест → регистрация

Environment:
  ANTHROPIC_API_KEY - API ключ для Claude

Examples:
  python tools/ats_parser_generator.py generate phenom --save
  python tools/ats_parser_generator.py register phenom
  python tools/ats_parser_generator.py auto
        """)
        return

    command = sys.argv[1]

    if command == "analyze" and len(sys.argv) > 2:
        ats_name = sys.argv[2]
        ats_data = get_ats_data(ats_name)

        if not ats_data:
            print(f"❌ No data found for: {ats_name}")
            return

        print(f"\n📊 Data for: {ats_name}")
        print(f"   Status: {ats_data.get('parser_status', 'not_started')}")
        print(f"   First seen: {ats_data.get('first_seen', 'N/A')}")
        print(f"\n   Companies ({len(ats_data.get('companies_using', []))}):")
        for c in ats_data.get("companies_using", [])[:5]:
            print(f"     - {c}")

        print(f"\n   Endpoints ({len(ats_data.get('api_endpoints', []))}):")
        for ep in ats_data.get("api_endpoints", [])[:10]:
            if isinstance(ep, dict):
                print(f"     - {ep.get('method', 'GET')} {ep.get('url', '')[:70]}...")

        print(f"\n   Sample responses ({len(ats_data.get('sample_responses', []))}):")
        for sample in ats_data.get("sample_responses", [])[:3]:
            if isinstance(sample, dict):
                print(f"     - {sample.get('url', '')[:50]}...")
                print(f"       Jobs array: {sample.get('has_jobs_array', False)}")

    elif command == "generate" and len(sys.argv) > 2:
        ats_name = sys.argv[2]
        auto_save = "--save" in sys.argv
        generate_parser(ats_name, auto_save=auto_save)

    elif command == "test" and len(sys.argv) > 2:
        ats_name = sys.argv[2]
        result = test_parser(ats_name)

        if result["ok"]:
            print(f"✅ Parser test passed")
            print(f"   Jobs found: {result['jobs_count']}")
            if result.get("output"):
                print(f"\n   Output:\n{result['output']}")
        else:
            print(f"❌ Parser test failed: {result['error']}")

    elif command == "register" and len(sys.argv) > 2:
        ats_name = sys.argv[2]
        result = register_parser_in_main(ats_name)

        if not result["ok"]:
            print(f"❌ Registration failed: {result['error']}")

    elif command == "unregister" and len(sys.argv) > 2:
        ats_name = sys.argv[2]
        result = unregister_parser_from_main(ats_name)

        if not result["ok"]:
            print(f"❌ Unregistration failed: {result['error']}")

    elif command == "auto":
        if not ANTHROPIC_API_KEY:
            print("❌ ANTHROPIC_API_KEY environment variable not set")
            print("   Export it or add to .env file")
            return
        auto_generate_all()

    else:
        print("Invalid command. Run without arguments for help.")


if __name__ == "__main__":
    main()
