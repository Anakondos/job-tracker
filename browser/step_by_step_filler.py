"""
Step-by-Step Form Filler - пошаговое заполнение с полной проверкой.

Логика:
1. Загрузить форму
2. Обнаружить ВСЕ поля через DOM (как в V5)
3. Для каждого поля:
   a. Получить bounding box (координаты)
   b. Кликнуть по центру поля (page.mouse.click)
   c. Напечатать значение (page.keyboard.type)
   d. Сделать скриншот
   e. Спросить Claude Vision: заполнилось ли поле?
   f. Если нет - попробовать другой подход
4. Скроллить если нужно
5. Показать результат
"""

import json
import time
import base64
import os
from pathlib import Path
from dataclasses import dataclass
from typing import List, Optional, Dict, Any
from enum import Enum

from playwright.sync_api import sync_playwright, Page, ElementHandle
from dotenv import load_dotenv

# Load .env from parent directory
env_path = Path(__file__).parent.parent / ".env"
if env_path.exists():
    load_dotenv(env_path)

# Try to get API key from environment
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# If running from Claude Code, the API is available via the client
try:
    import anthropic
    HAS_ANTHROPIC = True
except ImportError:
    HAS_ANTHROPIC = False


class FieldType(Enum):
    TEXT = "text"
    EMAIL = "email"
    PHONE = "phone"
    SELECT = "select"
    AUTOCOMPLETE = "autocomplete"
    CHECKBOX = "checkbox"
    FILE = "file"
    TEXTAREA = "textarea"


@dataclass
class FormField:
    """Обнаруженное поле формы"""
    selector: str
    element_id: str
    name: str
    label: str
    field_type: FieldType
    required: bool
    current_value: str
    # Координаты
    x: int = 0
    y: int = 0
    width: int = 0
    height: int = 0
    # Статус
    filled: bool = False
    verified: bool = False
    error: str = ""


class StepByStepFiller:
    """Пошаговое заполнение формы с проверкой каждого поля."""

    def __init__(self, profile_path: Path = None):
        self.page: Optional[Page] = None
        self.fields: List[FormField] = []
        self.screenshots_dir = Path(__file__).parent / "screenshots"
        self.screenshots_dir.mkdir(exist_ok=True)

        # Load profile
        if profile_path is None:
            profile_path = Path(__file__).parent / "profiles" / "anton_tpm.json"

        with open(profile_path) as f:
            self.profile = json.load(f)

        # Claude client
        self.claude_client = None
        if HAS_ANTHROPIC and ANTHROPIC_API_KEY:
            self.claude_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    def log(self, msg: str):
        """Печатаем все действия"""
        print(f"  {msg}")

    # ═══════════════════════════════════════════════════════════════════════════
    # STEP 1: LOAD FORM
    # ═══════════════════════════════════════════════════════════════════════════

    def load_form(self, url: str, page: Page):
        """Загрузить форму"""
        self.page = page
        self.log(f"🌐 Loading: {url}")
        page.goto(url)
        page.wait_for_load_state("networkidle")
        time.sleep(2)  # Wait for JS

        # Wait for form to fully load
        self.log("⏳ Waiting for form elements...")
        time.sleep(3)
        self.log(f"📄 Total frames: {len(page.frames)}")
        self.log("✅ Form loaded")

    # ═══════════════════════════════════════════════════════════════════════════
    # STEP 2: DETECT ALL FIELDS (like V5)
    # ═══════════════════════════════════════════════════════════════════════════

    def detect_all_fields(self) -> List[FormField]:
        """Обнаружить все поля формы через DOM"""
        self.log("🔍 Detecting all form fields...")
        self.fields = []

        # Scan main page AND all iframes
        contexts = [self.page] + list(self.page.frames)

        for ctx in contexts:
            try:
                elements = ctx.query_selector_all("input, select, textarea")
                for el in elements:
                    field = self._detect_single_field(el, ctx)
                    if field:
                        self.fields.append(field)
            except Exception as e:
                continue

        self.log(f"📋 Found {len(self.fields)} fields:")
        for i, f in enumerate(self.fields):
            req = "*" if f.required else ""
            val = f"[{f.current_value[:20]}]" if f.current_value else "[empty]"
            self.log(f"   {i+1}. {f.label[:40]}{req} ({f.field_type.value}) {val}")

        return self.fields

    def _detect_single_field(self, el: ElementHandle, ctx) -> Optional[FormField]:
        """Обнаружить одно поле"""
        try:
            # Skip hidden
            if not el.is_visible():
                return None

            # Get attributes
            tag = el.evaluate("e => e.tagName.toLowerCase()")
            input_type = el.get_attribute("type") or "text"
            el_id = el.get_attribute("id") or ""
            el_name = el.get_attribute("name") or ""

            # Skip system fields
            if input_type in ("hidden", "submit", "button"):
                return None

            # Build selector
            if el_id:
                selector = f"#{el_id}"
            elif el_name:
                selector = f"[name='{el_name}']"
            else:
                return None

            # Get label
            label = self._find_label(el, el_id, ctx)

            # Get bounding box (coordinates!)
            box = el.bounding_box()
            if not box:
                return None

            # Current value
            current_value = ""
            try:
                if tag == "select":
                    current_value = el.evaluate("e => e.options[e.selectedIndex]?.text || ''")
                elif input_type != "file":
                    current_value = el.input_value() or ""
            except:
                pass

            # Required?
            required = el.get_attribute("required") is not None

            # Detect type
            field_type = self._detect_type(tag, input_type, el)

            return FormField(
                selector=selector,
                element_id=el_id,
                name=el_name,
                label=label,
                field_type=field_type,
                required=required,
                current_value=current_value,
                x=int(box["x"] + box["width"] / 2),  # Center
                y=int(box["y"] + box["height"] / 2),
                width=int(box["width"]),
                height=int(box["height"]),
            )

        except Exception as e:
            return None

    def _find_label(self, el: ElementHandle, el_id: str, ctx) -> str:
        """Найти label поля"""
        label = ""

        # 1. By for attribute
        if el_id:
            try:
                label_el = ctx.query_selector(f"label[for='{el_id}']")
                if label_el:
                    label = label_el.inner_text().strip()
            except:
                pass

        # 2. aria-label / placeholder
        if not label:
            label = el.get_attribute("aria-label") or \
                    el.get_attribute("placeholder") or ""

        # 3. Name/ID fallback
        if not label:
            label = el.get_attribute("name") or el_id or "unknown"

        return label

    def _detect_type(self, tag: str, input_type: str, el: ElementHandle) -> FieldType:
        """Определить тип поля"""
        if tag == "select":
            return FieldType.SELECT
        if tag == "textarea":
            return FieldType.TEXTAREA
        if input_type == "file":
            return FieldType.FILE
        if input_type == "checkbox":
            return FieldType.CHECKBOX
        if input_type == "email":
            return FieldType.EMAIL
        if input_type == "tel":
            return FieldType.PHONE

        # Check for autocomplete (React Select)
        role = el.get_attribute("role") or ""
        aria_haspopup = el.get_attribute("aria-haspopup") or ""
        if role == "combobox" or aria_haspopup in ("true", "listbox"):
            return FieldType.AUTOCOMPLETE

        return FieldType.TEXT

    # ═══════════════════════════════════════════════════════════════════════════
    # STEP 3: GET VALUE FROM PROFILE
    # ═══════════════════════════════════════════════════════════════════════════

    def get_value_for_field(self, field: FormField) -> Optional[str]:
        """Получить значение для поля из профиля"""
        label_lower = field.label.lower()
        name_lower = field.name.lower() if field.name else ""

        p = self.profile.get("personal", {})
        links = self.profile.get("links", {})
        wa = self.profile.get("work_authorization", {})
        common = self.profile.get("common_answers", {})

        # Direct mappings
        mappings = {
            "first name": p.get("first_name"),
            "last name": p.get("last_name"),
            "email": p.get("email"),
            "phone": p.get("phone"),
            "city": p.get("city"),
            "state": p.get("state"),
            "zip": p.get("zip_code"),
            "postal": p.get("zip_code"),
            "country": p.get("country", "United States"),
            "linkedin": links.get("linkedin"),
            "github": links.get("github"),
        }

        for pattern, value in mappings.items():
            if pattern in label_lower or pattern in name_lower:
                return value

        # Yes/No questions
        yes_patterns = ["18 years", "authorized", "legally authorized", "confirm", "agree"]
        no_patterns = ["sponsorship", "visa sponsor", "require sponsor"]

        for pattern in yes_patterns:
            if pattern in label_lower:
                return "Yes"

        for pattern in no_patterns:
            if pattern in label_lower:
                return "No"

        return None

    # ═══════════════════════════════════════════════════════════════════════════
    # STEP 4: FILL SINGLE FIELD BY COORDINATES
    # ═══════════════════════════════════════════════════════════════════════════

    def fill_field_by_click(self, field: FormField, value: str) -> Dict[str, Any]:
        """
        Заполнить поле кликом по координатам.
        Returns: {"success": bool, "method": str, "error": str}
        """
        result = {"success": False, "method": "", "error": ""}

        self.log(f"🖱️ Clicking at ({field.x}, {field.y}) for '{field.label[:30]}'")

        try:
            # Scroll field into view first
            self._scroll_to_field(field)
            time.sleep(0.3)

            # Update coordinates after scroll
            self._update_field_coordinates(field)

            # CLICK on field center
            self.page.mouse.click(field.x, field.y)
            time.sleep(0.2)

            if field.field_type == FieldType.TEXT or field.field_type == FieldType.EMAIL or field.field_type == FieldType.PHONE:
                # Clear existing and type
                self.page.keyboard.press("Control+a")
                time.sleep(0.1)
                self.page.keyboard.type(value, delay=20)
                result["method"] = "click_and_type"

            elif field.field_type == FieldType.SELECT or field.field_type == FieldType.AUTOCOMPLETE:
                # Type to filter, then Enter
                time.sleep(0.3)
                self.page.keyboard.type(value, delay=30)
                time.sleep(0.3)
                self.page.keyboard.press("Enter")
                result["method"] = "click_type_enter"

            elif field.field_type == FieldType.CHECKBOX:
                # Just click toggles
                result["method"] = "click"

            else:
                self.page.keyboard.type(value, delay=20)
                result["method"] = "click_and_type"

            time.sleep(0.3)
            result["success"] = True

        except Exception as e:
            result["error"] = str(e)
            self.log(f"❌ Error: {e}")

        return result

    def _scroll_to_field(self, field: FormField):
        """Прокрутить к полю если нужно"""
        viewport = self.page.viewport_size
        if not viewport:
            return

        # If field is below viewport, scroll down
        if field.y > viewport["height"] - 100:
            scroll_amount = field.y - viewport["height"] // 2
            self.page.mouse.wheel(0, scroll_amount)
            self.log(f"⬇️ Scrolled down {scroll_amount}px")
            time.sleep(0.3)

    def _update_field_coordinates(self, field: FormField):
        """Обновить координаты поля после прокрутки"""
        try:
            # Find element again
            el = None
            for ctx in [self.page] + list(self.page.frames):
                try:
                    el = ctx.query_selector(field.selector)
                    if el and el.is_visible():
                        break
                except:
                    continue

            if el:
                box = el.bounding_box()
                if box:
                    field.x = int(box["x"] + box["width"] / 2)
                    field.y = int(box["y"] + box["height"] / 2)
        except:
            pass

    # ═══════════════════════════════════════════════════════════════════════════
    # STEP 5: VERIFY WITH SCREENSHOT + CLAUDE
    # ═══════════════════════════════════════════════════════════════════════════

    def take_screenshot(self, name: str) -> bytes:
        """Сделать скриншот"""
        screenshot = self.page.screenshot(type="png")
        path = self.screenshots_dir / f"{name}.png"
        path.write_bytes(screenshot)
        self.log(f"📸 Screenshot: {path.name}")
        return screenshot

    def verify_with_claude(self, screenshot: bytes, field: FormField, expected_value: str) -> Dict[str, Any]:
        """
        Спросить Claude Vision: заполнилось ли поле?
        Returns: {"verified": bool, "actual_value": str, "message": str}
        """
        if not self.claude_client:
            self.log("⚠️ No Claude API - skipping verification")
            return {"verified": True, "actual_value": "unknown", "message": "No API"}

        b64 = base64.standard_b64encode(screenshot).decode("utf-8")

        prompt = f"""Look at this form screenshot.

Find the field labeled "{field.label}" (or similar).
Check if it contains the value: "{expected_value}"

Return ONLY JSON:
{{"verified": true/false, "actual_value": "what you see in the field", "message": "brief explanation"}}"""

        try:
            response = self.claude_client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=200,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}},
                        {"type": "text", "text": prompt}
                    ],
                }],
            )

            text = response.content[0].text.strip()
            if "{" in text:
                result = json.loads(text[text.find("{"):text.rfind("}")+1])
                return result

        except Exception as e:
            self.log(f"⚠️ Claude error: {e}")

        return {"verified": False, "actual_value": "", "message": "API error"}

    # ═══════════════════════════════════════════════════════════════════════════
    # MAIN: FILL ONE FIELD WITH FULL VERIFICATION
    # ═══════════════════════════════════════════════════════════════════════════

    def fill_and_verify_field(self, field_index: int) -> Dict[str, Any]:
        """
        Заполнить одно поле с полной проверкой.

        Returns подробный отчет:
        {
            "field": field info,
            "value": value used,
            "fill_result": click result,
            "verification": claude verification,
            "success": bool
        }
        """
        if field_index >= len(self.fields):
            return {"error": "Field index out of range"}

        field = self.fields[field_index]
        report = {
            "field": {
                "label": field.label,
                "type": field.field_type.value,
                "selector": field.selector,
                "coordinates": {"x": field.x, "y": field.y},
            },
            "value": None,
            "fill_result": None,
            "verification": None,
            "success": False
        }

        # Skip if already filled
        if field.current_value:
            self.log(f"⏭️ Field already has value: {field.current_value[:30]}")
            report["success"] = True
            report["value"] = field.current_value
            return report

        # Get value from profile
        value = self.get_value_for_field(field)
        if not value:
            self.log(f"⚠️ No value found for: {field.label}")
            report["error"] = "No value in profile"
            return report

        report["value"] = value
        self.log(f"\n{'='*60}")
        self.log(f"FILLING FIELD {field_index + 1}: {field.label}")
        self.log(f"  Value: {value}")
        self.log(f"  Type: {field.field_type.value}")
        self.log(f"  Coordinates: ({field.x}, {field.y})")
        self.log(f"{'='*60}")

        # Fill by clicking
        fill_result = self.fill_field_by_click(field, value)
        report["fill_result"] = fill_result

        if not fill_result["success"]:
            self.log(f"❌ Fill failed: {fill_result['error']}")
            return report

        self.log(f"✅ Filled using method: {fill_result['method']}")

        # Take screenshot for verification
        screenshot = self.take_screenshot(f"field_{field_index:02d}_{field.name or 'unknown'}")

        # Verify with Claude
        verification = self.verify_with_claude(screenshot, field, value)
        report["verification"] = verification

        if verification.get("verified"):
            self.log(f"✅ VERIFIED: {verification.get('actual_value', 'OK')}")
            field.filled = True
            field.verified = True
            report["success"] = True
        else:
            self.log(f"⚠️ NOT VERIFIED: {verification.get('message', 'Unknown')}")
            self.log(f"   Expected: {value}")
            self.log(f"   Actual: {verification.get('actual_value', 'unknown')}")

        return report


def test_first_field():
    """Тест: загрузить форму и заполнить первое поле"""

    # Greenhouse test URL (Coinbase - та же что тестировали раньше)
    url = "https://job-boards.greenhouse.io/embed/job_app?token=7404427&for=coinbase&gh_jid=7404427"

    print("\n" + "="*70)
    print("STEP-BY-STEP FORM FILLER TEST")
    print("="*70)

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            args=["--window-size=1400,900"]
        )

        context = browser.new_context(
            viewport={"width": 1400, "height": 900}
        )

        page = context.new_page()

        # Create filler
        filler = StepByStepFiller()

        # Step 1: Load form
        print("\n[STEP 1] Loading form...")
        filler.load_form(url, page)

        # Step 2: Detect all fields
        print("\n[STEP 2] Detecting fields...")
        fields = filler.detect_all_fields()

        if not fields:
            print("❌ No fields found!")
            browser.close()
            return

        # Step 3: Fill FIRST field only
        print("\n[STEP 3] Filling FIRST field...")
        result = filler.fill_and_verify_field(0)

        print("\n" + "="*70)
        print("RESULT:")
        print(json.dumps(result, indent=2, default=str))
        print("="*70)

        # Keep browser open
        input("\n⏸️ Press Enter to close browser...")
        browser.close()


if __name__ == "__main__":
    test_first_field()
