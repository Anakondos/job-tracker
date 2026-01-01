"""
Smart Filler V3.1 - Probe First, Fill Second

Key improvements:
- PROBE each field first to detect real type
- Detect Greenhouse-style dropdowns (input + combobox)  
- Read actual options from dropdowns
- For dropdown: choose from options, NOT generate text
- Highlight current field in browser
- Better Yes/No detection for compliance questions
"""

import json
import re
import time
import requests
from pathlib import Path
from typing import Optional, List, Tuple
from dataclasses import dataclass, field as dataclass_field
from enum import Enum

from playwright.sync_api import sync_playwright, Page, ElementHandle


# Paths
BROWSER_DIR = Path(__file__).parent
DATABASE_PATH = BROWSER_DIR / "learned_database_v3.json"
PROFILE_PATH = BROWSER_DIR / "profiles" / "anton_tpm.json"


class FieldType(Enum):
    """Real field types after probing."""
    TEXT = "text"
    TEXTAREA = "textarea"
    EMAIL = "email"
    PHONE = "phone"
    DROPDOWN = "dropdown"        # Select or combobox with options
    CHECKBOX = "checkbox"
    RADIO = "radio"
    DATE = "date"
    FILE = "file"
    UNKNOWN = "unknown"


@dataclass
class ProbedField:
    """Field with full probe results."""
    selector: str
    label: str
    field_type: FieldType
    html_tag: str               # input, select, textarea
    input_type: str             # text, email, checkbox, etc
    
    # Probe results
    is_dropdown: bool = False
    options: List[str] = dataclass_field(default_factory=list)
    is_required: bool = False
    is_editable: bool = True
    current_value: str = ""
    
    # For processing
    answer: str = ""
    answer_source: str = ""     # profile, database, ai, user
    filled: bool = False
    verified: bool = False


class Database:
    """Learning database."""
    
    def __init__(self, path: Path = DATABASE_PATH):
        self.path = path
        self.data = self._load()
    
    def _load(self) -> dict:
        if self.path.exists():
            with open(self.path, "r") as f:
                return json.load(f)
        return {"answers": {}, "dropdown_choices": {}}
    
    def save(self):
        with open(self.path, "w") as f:
            json.dump(self.data, f, indent=2, ensure_ascii=False)
    
    def _key(self, label: str) -> str:
        """Normalize label to key."""
        key = label.lower().strip()
        key = re.sub(r'[*?!:\-_()\"\']+', ' ', key)
        key = re.sub(r'\s+', ' ', key).strip()
        return key[:100]  # Limit length
    
    def find_answer(self, label: str) -> Optional[str]:
        """Find saved answer."""
        key = self._key(label)
        
        # Exact
        if key in self.data["answers"]:
            return self.data["answers"][key]
        
        # Partial match
        for k, v in self.data["answers"].items():
            if k in key or key in k:
                return v
        return None
    
    def find_dropdown_choice(self, label: str) -> Optional[str]:
        """Find saved dropdown choice."""
        key = self._key(label)
        
        if key in self.data["dropdown_choices"]:
            return self.data["dropdown_choices"][key]
        
        for k, v in self.data["dropdown_choices"].items():
            if k in key or key in k:
                return v
        return None
    
    def save_answer(self, label: str, answer: str):
        """Save text answer."""
        key = self._key(label)
        self.data["answers"][key] = answer
        self.save()
        print(f"   💾 Saved: '{label[:35]}' → '{answer[:25]}'")
    
    def save_dropdown_choice(self, label: str, choice: str):
        """Save dropdown choice."""
        key = self._key(label)
        self.data["dropdown_choices"][key] = choice
        self.save()
        print(f"   💾 Saved dropdown: '{label[:35]}' → '{choice}'")


class Profile:
    """User profile."""
    
    MAPPINGS = {
        "first name": "personal.first_name",
        "last name": "personal.last_name",
        "email": "personal.email",
        "phone": "personal.phone",
        "location": "personal.location",
        "city": "personal.city",
        "country": "personal.country",
        "linkedin": "links.linkedin",
        "github": "links.github",
        "company name": "work_experience.0.company",
        "job title": "work_experience.0.title",
        "title": "work_experience.0.title",
        "start date month": "work_experience.0.start_month",
        "start date year": "work_experience.0.start_year",
        "school": "education.0.school",
        "degree": "education.0.degree",
        "discipline": "education.0.discipline",
    }
    
    # Default answers for Yes/No dropdowns
    YES_NO_DEFAULTS = {
        # Standard work auth
        "18 years": "Yes",
        "authorized to work": "Yes",
        "legally authorized": "Yes",
        "require sponsorship": "No",
        "visa sponsorship": "No",
        
        # Compliance - usually No
        "government official": "No",
        "close relative of a government": "No",
        "conflict of interest": "No",
        "connected to": "No",
        "financial interest": "No",
        "referred to this position by": "No",
        "senior leader": "No",
        
        # Previous employment
        "previously employed": "No",
        "former employee": "No",
        
        # Confirmations
        "confirm receipt": "Yes",
        "acknowledge": "Yes",
        "agree": "Yes",
        "i understand": "Yes",
    }
    
    DEMOGRAPHIC_DEFAULTS = {
        "gender": "Decline",
        "race": "Decline",
        "ethnicity": "Decline",
        "hispanic": "Decline",
        "latino": "Decline",
        "veteran": "not a protected veteran",
        "disability": "do not want to answer",
    }
    
    def __init__(self, path: Path = PROFILE_PATH):
        if path.exists():
            with open(path) as f:
                self.data = json.load(f)
        else:
            self.data = {}
    
    def get(self, path: str) -> str:
        """Get value by dot path."""
        parts = path.split(".")
        val = self.data
        for p in parts:
            if val is None:
                return ""
            if p.isdigit():
                val = val[int(p)] if isinstance(val, list) and int(p) < len(val) else None
            else:
                val = val.get(p) if isinstance(val, dict) else None
        return str(val) if val else ""
    
    def find_for_label(self, label: str) -> Optional[str]:
        """Find profile value for label."""
        ll = label.lower()
        for pattern, path in self.MAPPINGS.items():
            if pattern in ll:
                val = self.get(path)
                if val:
                    return val
        return None
    
    def find_yes_no_default(self, label: str) -> Optional[str]:
        """Find default Yes/No answer."""
        ll = label.lower()
        for pattern, answer in self.YES_NO_DEFAULTS.items():
            if pattern in ll:
                return answer
        return None
    
    def find_demographic_default(self, label: str) -> Optional[str]:
        """Find demographic default."""
        ll = label.lower()
        for pattern, answer in self.DEMOGRAPHIC_DEFAULTS.items():
            if pattern in ll:
                return answer
        return None
    
    def get_context(self) -> str:
        """Get context for AI."""
        p = self.data.get("personal", {})
        w = self.data.get("work_experience", [{}])[0]
        return f"""Name: {p.get('first_name', '')} {p.get('last_name', '')}
Location: {p.get('location', '')}
Current: {w.get('title', '')} at {w.get('company', '')}
Experience: 15+ years in Product/Program Management"""


class TextAI:
    """Text AI for generating answers."""
    
    def __init__(self, model: str = "llama3.2:3b"):
        self.model = model
        self.url = "http://localhost:11434"
        self.available = self._check()
    
    def _check(self) -> bool:
        try:
            return requests.get(f"{self.url}/api/tags", timeout=3).ok
        except:
            return False
    
    def generate(self, question: str, context: str) -> str:
        """Generate answer."""
        if not self.available:
            return ""
        
        try:
            resp = requests.post(
                f"{self.url}/api/generate",
                json={
                    "model": self.model,
                    "prompt": f"""Job application question: {question}

Profile: {context}

Write a brief, professional answer (1-2 sentences):""",
                    "stream": False,
                    "options": {"temperature": 0.3, "num_predict": 100}
                },
                timeout=30
            )
            if resp.ok:
                return resp.json().get("response", "").strip()
        except:
            pass
        return ""
    
    def choose_option(self, question: str, options: List[str], context: str) -> Optional[str]:
        """Choose best option from list."""
        if not self.available or not options:
            return None
        
        try:
            resp = requests.post(
                f"{self.url}/api/generate",
                json={
                    "model": self.model,
                    "prompt": f"""Question: {question}

Options: {', '.join(options)}

Profile: {context}

Which option is correct? Reply with ONLY the exact option text, nothing else:""",
                    "stream": False,
                    "options": {"temperature": 0.1, "num_predict": 50}
                },
                timeout=20
            )
            if resp.ok:
                answer = resp.json().get("response", "").strip()
                # Find matching option
                for opt in options:
                    if opt.lower() in answer.lower() or answer.lower() in opt.lower():
                        return opt
        except:
            pass
        return None


class SmartFillerV31:
    """Smart Filler with Probe-First approach."""
    
    def __init__(self, headless: bool = False):
        self.headless = headless
        self.db = Database()
        self.profile = Profile()
        self.ai = TextAI()
        
        self.playwright = None
        self.browser = None
        self.page: Page = None
        
        self.fields: List[ProbedField] = []
        self.stats = {"auto": 0, "user": 0, "learned": 0, "skipped": 0}
    
    # ─────────────────────────────────────────────────────────────
    # BROWSER
    # ─────────────────────────────────────────────────────────────
    
    def start(self):
        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch(
            headless=self.headless,
            slow_mo=50 if not self.headless else 0
        )
        self.page = self.browser.new_page(viewport={"width": 1400, "height": 900})
    
    def stop(self):
        if self.browser:
            self.browser.close()
        if self.playwright:
            self.playwright.stop()
    
    def goto(self, url: str):
        print(f"\n🌐 Opening: {url[:60]}...")
        self.page.goto(url, wait_until="networkidle")
        time.sleep(2)
        print(f"📄 Page: {self.page.title()[:50]}")
    
    # ─────────────────────────────────────────────────────────────
    # HIGHLIGHT
    # ─────────────────────────────────────────────────────────────
    
    def highlight(self, selector: str, color: str = "red"):
        """Highlight field in browser."""
        try:
            self.page.evaluate(f"""
                const el = document.querySelector('{selector}');
                if (el) {{
                    el.style.outline = '3px solid {color}';
                    el.scrollIntoView({{behavior: 'smooth', block: 'center'}});
                }}
            """)
        except:
            pass
    
    def unhighlight(self, selector: str):
        """Remove highlight."""
        try:
            self.page.evaluate(f"""
                const el = document.querySelector('{selector}');
                if (el) el.style.outline = '';
            """)
        except:
            pass
    
    # ─────────────────────────────────────────────────────────────
    # PROBE - Detect real field type
    # ─────────────────────────────────────────────────────────────
    
    def probe_field(self, el: ElementHandle, selector: str) -> Optional[ProbedField]:
        """Probe a field to detect its real type."""
        try:
            # Basic attributes
            tag = el.evaluate("e => e.tagName.toLowerCase()")
            el_type = el.get_attribute("type") or "text"
            role = el.get_attribute("role") or ""
            aria_haspopup = el.get_attribute("aria-haspopup") or ""
            required = el.get_attribute("required") is not None or \
                       el.get_attribute("aria-required") == "true"
            
            # Skip hidden/system
            if el_type in ("hidden", "submit", "button"):
                return None
            if not el.is_visible():
                return None
            
            # Get label
            el_id = el.get_attribute("id") or ""
            label = ""
            if el_id:
                label_el = self.page.query_selector(f"label[for='{el_id}']")
                if label_el:
                    label = label_el.inner_text().strip()
            if not label:
                label = el.get_attribute("aria-label") or \
                        el.get_attribute("placeholder") or \
                        el.get_attribute("name") or el_id
            
            # Get current value
            current = ""
            try:
                if tag == "select":
                    current = el.evaluate("e => e.options[e.selectedIndex]?.text || ''")
                elif el_type == "checkbox":
                    current = "checked" if el.is_checked() else ""
                else:
                    current = el.input_value() or ""
            except:
                pass
            
            # Detect if it's a dropdown
            is_dropdown = False
            options = []
            
            # Real <select>
            if tag == "select":
                is_dropdown = True
                options = el.evaluate("e => Array.from(e.options).map(o => o.text).filter(t => t && t !== 'Select...')")
            
            # Greenhouse-style combobox (input with role="combobox")
            elif role == "combobox" or aria_haspopup in ("listbox", "true"):
                is_dropdown = True
                # Need to click to get options
                options = self._probe_combobox_options(el, selector)
            
            # Determine field type
            if el_type == "checkbox":
                ftype = FieldType.CHECKBOX
            elif el_type == "file":
                ftype = FieldType.FILE
            elif el_type == "email":
                ftype = FieldType.EMAIL
            elif el_type == "tel":
                ftype = FieldType.PHONE
            elif el_type == "date":
                ftype = FieldType.DATE
            elif is_dropdown:
                ftype = FieldType.DROPDOWN
            elif tag == "textarea":
                ftype = FieldType.TEXTAREA
            else:
                ftype = FieldType.TEXT
            
            return ProbedField(
                selector=selector,
                label=label,
                field_type=ftype,
                html_tag=tag,
                input_type=el_type,
                is_dropdown=is_dropdown,
                options=options,
                is_required=required,
                is_editable=el.is_editable(),
                current_value=current
            )
            
        except Exception as e:
            return None
    
    def _probe_combobox_options(self, el: ElementHandle, selector: str) -> List[str]:
        """Click combobox to reveal options, then read them."""
        options = []
        try:
            # Click to open
            el.click()
            time.sleep(0.3)
            
            # Look for listbox/options
            listbox = self.page.query_selector("[role='listbox'], .select__menu, [class*='menu']")
            if listbox:
                option_els = listbox.query_selector_all("[role='option'], .select__option, [class*='option']")
                for opt in option_els:
                    text = opt.inner_text().strip()
                    if text and text not in ("Select...", ""):
                        options.append(text)
            
            # Close dropdown
            self.page.keyboard.press("Escape")
            time.sleep(0.1)
            
        except:
            try:
                self.page.keyboard.press("Escape")
            except:
                pass
        
        return options[:20]  # Limit
    
    # ─────────────────────────────────────────────────────────────
    # SCAN all fields
    # ─────────────────────────────────────────────────────────────
    
    def scan(self) -> List[ProbedField]:
        """Scan and probe all fields."""
        print("\n🔍 Scanning & probing fields...")
        
        elements = self.page.query_selector_all("input, select, textarea")
        
        for el in elements:
            el_id = el.get_attribute("id") or ""
            el_name = el.get_attribute("name") or ""
            
            if el_id:
                selector = f"#{el_id}"
            elif el_name:
                selector = f"[name='{el_name}']"
            else:
                continue
            
            field = self.probe_field(el, selector)
            if field:
                self.fields.append(field)
        
        # Stats
        dropdowns = sum(1 for f in self.fields if f.is_dropdown)
        texts = sum(1 for f in self.fields if f.field_type == FieldType.TEXT)
        print(f"   Found: {len(self.fields)} fields ({dropdowns} dropdowns, {texts} text)")
        
        return self.fields
    
    # ─────────────────────────────────────────────────────────────
    # FILL field
    # ─────────────────────────────────────────────────────────────
    
    def fill_field(self, field: ProbedField, value: str) -> bool:
        """Fill a field with value."""
        try:
            el = self.page.query_selector(field.selector)
            if not el:
                return False
            
            el.scroll_into_view_if_needed()
            time.sleep(0.1)
            
            if field.is_dropdown:
                # Dropdown - click, type to filter, select
                el.click()
                time.sleep(0.2)
                
                # Type partial to filter
                el.type(value[:15], delay=30)
                time.sleep(0.3)
                
                # Select first match
                self.page.keyboard.press("ArrowDown")
                time.sleep(0.1)
                self.page.keyboard.press("Enter")
                time.sleep(0.2)
                
            elif field.field_type == FieldType.CHECKBOX:
                should_check = value.lower() in ("yes", "true", "1", "checked")
                if should_check != el.is_checked():
                    el.click()
                    
            else:
                # Text input
                el.fill(value)
            
            field.filled = True
            return True
            
        except Exception as e:
            print(f"   ❌ Fill error: {e}")
            return False
    
    def read_field(self, field: ProbedField) -> str:
        """Read current value of field."""
        try:
            el = self.page.query_selector(field.selector)
            if not el:
                return ""
            
            if field.html_tag == "select":
                return el.evaluate("e => e.options[e.selectedIndex]?.text || ''")
            elif field.field_type == FieldType.CHECKBOX:
                return "checked" if el.is_checked() else ""
            else:
                return el.input_value() or ""
        except:
            return ""
    
    # ─────────────────────────────────────────────────────────────
    # FIND ANSWER for field
    # ─────────────────────────────────────────────────────────────
    
    def find_answer(self, field: ProbedField) -> Tuple[Optional[str], str]:
        """
        Find answer for field.
        Returns: (answer, source) or (None, "")
        """
        label = field.label
        
        # 1. Check database
        if field.is_dropdown:
            saved = self.db.find_dropdown_choice(label)
            if saved:
                return saved, "database"
        else:
            saved = self.db.find_answer(label)
            if saved:
                return saved, "database"
        
        # 2. Profile mappings (for text fields)
        if not field.is_dropdown:
            profile_val = self.profile.find_for_label(label)
            if profile_val:
                return profile_val, "profile"
        
        # 3. For dropdowns - check Yes/No defaults
        if field.is_dropdown and field.options:
            # Check if it's a Yes/No question
            has_yes = any("yes" in o.lower() for o in field.options)
            has_no = any("no" in o.lower() for o in field.options)
            
            if has_yes or has_no:
                # Check our defaults
                default = self.profile.find_yes_no_default(label)
                if default:
                    # Find matching option
                    for opt in field.options:
                        if default.lower() in opt.lower():
                            return opt, "default"
            
            # Check demographic defaults
            demo_default = self.profile.find_demographic_default(label)
            if demo_default:
                for opt in field.options:
                    if demo_default.lower() in opt.lower():
                        return opt, "default"
        
        return None, ""
    
    # ─────────────────────────────────────────────────────────────
    # PROCESS form
    # ─────────────────────────────────────────────────────────────
    
    def process(self, interactive: bool = True):
        """Process all fields."""
        print("\n📝 Processing fields...")
        
        new_fields = []  # Need user review
        
        for field in self.fields:
            # Skip file uploads
            if field.field_type == FieldType.FILE:
                print(f"   📎 {field.label[:40]:<40} (file - skip)")
                self.stats["skipped"] += 1
                continue
            
            # Skip already filled
            if field.current_value and field.current_value not in ("", "Select...", "Select"):
                continue
            
            # Find answer
            answer, source = self.find_answer(field)
            
            if answer:
                # Auto-fill
                self.highlight(field.selector, "green")
                self.fill_field(field, answer)
                self.unhighlight(field.selector)
                
                field.answer = answer
                field.answer_source = source
                self.stats["auto"] += 1
                
                display = f"({source})" if source != "database" else "(DB)"
                print(f"   ✅ {field.label[:40]:<40} = {answer[:20]} {display}")
            else:
                # New field - need review
                new_fields.append(field)
        
        # Interactive review
        if interactive and new_fields:
            self._interactive_review(new_fields)
        
        self._print_summary()
    
    def _interactive_review(self, fields: List[ProbedField]):
        """Interactive review of new fields."""
        print("\n" + "="*65)
        print(f"🎓 LEARNING MODE: {len(fields)} new fields")
        print("="*65)
        
        for field in fields:
            self.highlight(field.selector, "orange")
            
            print(f"\n📌 Field: \"{field.label[:60]}\"")
            print(f"   Type: {field.field_type.value}", end="")
            
            if field.is_dropdown and field.options:
                print(f" (dropdown with {len(field.options)} options)")
                print(f"   Options:")
                for i, opt in enumerate(field.options[:10], 1):
                    print(f"      {i}. {opt}")
                if len(field.options) > 10:
                    print(f"      ... and {len(field.options)-10} more")
                
                # AI suggestion for dropdown
                if self.ai.available:
                    suggested = self.ai.choose_option(
                        field.label, 
                        field.options, 
                        self.profile.get_context()
                    )
                    if suggested:
                        print(f"   💡 AI suggests: {suggested}")
                
                print(f"\n   Enter number (1-{len(field.options)}), or 's' to skip:")
                user = input("   > ").strip()
                
                if user.lower() == 's':
                    self.stats["skipped"] += 1
                    print("   ⏭️  Skipped")
                elif user.isdigit() and 1 <= int(user) <= len(field.options):
                    choice = field.options[int(user)-1]
                    self.fill_field(field, choice)
                    self.db.save_dropdown_choice(field.label, choice)
                    self.stats["learned"] += 1
                    self.stats["user"] += 1
                else:
                    print("   ⏭️  Invalid, skipped")
                    self.stats["skipped"] += 1
                    
            else:
                # Text field
                print(" (text)")
                
                # AI suggestion
                suggested = ""
                if self.ai.available:
                    suggested = self.ai.generate(field.label, self.profile.get_context())
                    if suggested:
                        print(f"   💡 AI suggests: {suggested[:60]}...")
                        self.fill_field(field, suggested)
                
                print(f"\n   Edit in browser if needed, then press ENTER")
                print(f"   Or type 's' to skip:")
                user = input("   > ").strip()
                
                if user.lower() == 's':
                    self.stats["skipped"] += 1
                    print("   ⏭️  Skipped")
                else:
                    # Read what user left
                    final = self.read_field(field)
                    if final:
                        self.db.save_answer(field.label, final)
                        self.stats["learned"] += 1
                        self.stats["user"] += 1
                    else:
                        self.stats["skipped"] += 1
            
            self.unhighlight(field.selector)
    
    def _print_summary(self):
        """Print session summary."""
        print("\n" + "="*65)
        print("📊 SUMMARY")
        print("="*65)
        print(f"   Auto-filled:  {self.stats['auto']}")
        print(f"   User filled:  {self.stats['user']}")
        print(f"   Learned:      {self.stats['learned']}")
        print(f"   Skipped:      {self.stats['skipped']}")
        print("="*65)
    
    # ─────────────────────────────────────────────────────────────
    # MAIN
    # ─────────────────────────────────────────────────────────────
    
    def run(self, url: str, interactive: bool = True):
        """Main entry point."""
        try:
            self.start()
            self.goto(url)
            self.scan()
            self.process(interactive)
            
            if not self.headless:
                print("\n👀 Review form in browser. Submit when ready.")
                print("   Press ENTER to close browser...")
                input()
        finally:
            self.stop()


# ─────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────

def main():
    import sys
    
    url = sys.argv[1] if len(sys.argv) > 1 else \
        "https://job-boards.greenhouse.io/embed/job_app?token=7404427&for=coinbase&gh_jid=7404427"
    
    print("\n" + "="*65)
    print("🚀 SMART FILLER V3.1 - Probe First, Fill Second")
    print("="*65)
    
    filler = SmartFillerV31(headless=False)
    filler.run(url, interactive=True)


if __name__ == "__main__":
    main()
