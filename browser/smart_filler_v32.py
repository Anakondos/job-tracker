"""
Smart Filler V3.2 - Complete Form Filler

Features from V3.1:
- Probe fields to detect real type
- Greenhouse dropdowns support
- Yes/No auto-defaults
- Interactive learning

NEW in V3.2:
- FILE UPLOAD (resume, cover letter)
- MULTI-PASS (dynamic fields)
- WAIT FOR JS (React forms)
- FINAL VERIFICATION report
"""

import json
import re
import time
import requests
from pathlib import Path
from typing import Optional, List, Tuple, Set
from dataclasses import dataclass, field as dataclass_field
from enum import Enum

from playwright.sync_api import sync_playwright, Page, ElementHandle


# ═══════════════════════════════════════════════════════════════════
# PATHS & CONFIG
# ═══════════════════════════════════════════════════════════════════

BROWSER_DIR = Path(__file__).parent
DATABASE_PATH = BROWSER_DIR / "learned_database_v3.json"
PROFILE_PATH = BROWSER_DIR / "profiles" / "anton_tpm.json"

# Resume file
RESUME_PATH = Path("/Users/antonkondakov/Library/Mobile Documents/com~apple~CloudDocs/Dev/AI_projects/Gold CV/Anton_Kondakov_TPM_CV.pdf")


class FieldType(Enum):
    TEXT = "text"
    TEXTAREA = "textarea"
    EMAIL = "email"
    PHONE = "phone"
    DROPDOWN = "dropdown"
    CHECKBOX = "checkbox"
    RADIO = "radio"
    DATE = "date"
    FILE = "file"
    UNKNOWN = "unknown"


@dataclass
class ProbedField:
    selector: str
    label: str
    field_type: FieldType
    html_tag: str
    input_type: str
    is_dropdown: bool = False
    options: List[str] = dataclass_field(default_factory=list)
    is_required: bool = False
    is_editable: bool = True
    current_value: str = ""
    answer: str = ""
    answer_source: str = ""
    filled: bool = False


# ═══════════════════════════════════════════════════════════════════
# DATABASE
# ═══════════════════════════════════════════════════════════════════

class Database:
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
        key = label.lower().strip()
        key = re.sub(r'[*?!:\-_()\"\']+', ' ', key)
        key = re.sub(r'\s+', ' ', key).strip()
        return key[:100]
    
    def find_answer(self, label: str) -> Optional[str]:
        key = self._key(label)
        if key in self.data["answers"]:
            return self.data["answers"][key]
        for k, v in self.data["answers"].items():
            if k in key or key in k:
                return v
        return None
    
    def find_dropdown_choice(self, label: str) -> Optional[str]:
        key = self._key(label)
        if key in self.data["dropdown_choices"]:
            return self.data["dropdown_choices"][key]
        for k, v in self.data["dropdown_choices"].items():
            if k in key or key in k:
                return v
        return None
    
    def save_answer(self, label: str, answer: str):
        key = self._key(label)
        self.data["answers"][key] = answer
        self.save()
        print(f"   💾 Saved: '{label[:35]}' → '{answer[:25]}'")
    
    def save_dropdown_choice(self, label: str, choice: str):
        key = self._key(label)
        self.data["dropdown_choices"][key] = choice
        self.save()
        print(f"   💾 Saved dropdown: '{label[:35]}' → '{choice}'")


# ═══════════════════════════════════════════════════════════════════
# PROFILE
# ═══════════════════════════════════════════════════════════════════

class Profile:
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
    
    YES_NO_DEFAULTS = {
        "18 years": "Yes",
        "authorized to work": "Yes",
        "legally authorized": "Yes",
        "require sponsorship": "No",
        "visa sponsorship": "No",
        "government official": "No",
        "close relative of a government": "No",
        "conflict of interest": "No",
        "connected to": "No",
        "financial interest": "No",
        "referred to this position by": "No",
        "senior leader": "No",
        "previously employed": "No",
        "former employee": "No",
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
        ll = label.lower()
        for pattern, path in self.MAPPINGS.items():
            if pattern in ll:
                val = self.get(path)
                if val:
                    return val
        return None
    
    def find_yes_no_default(self, label: str) -> Optional[str]:
        ll = label.lower()
        for pattern, answer in self.YES_NO_DEFAULTS.items():
            if pattern in ll:
                return answer
        return None
    
    def find_demographic_default(self, label: str) -> Optional[str]:
        ll = label.lower()
        for pattern, answer in self.DEMOGRAPHIC_DEFAULTS.items():
            if pattern in ll:
                return answer
        return None
    
    def get_context(self) -> str:
        p = self.data.get("personal", {})
        w = self.data.get("work_experience", [{}])[0]
        return f"""Name: {p.get('first_name', '')} {p.get('last_name', '')}
Location: {p.get('location', '')}
Current: {w.get('title', '')} at {w.get('company', '')}
Experience: 15+ years in Product/Program Management"""


# ═══════════════════════════════════════════════════════════════════
# TEXT AI
# ═══════════════════════════════════════════════════════════════════

class TextAI:
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

Which option is correct? Reply with ONLY the exact option text:""",
                    "stream": False,
                    "options": {"temperature": 0.1, "num_predict": 50}
                },
                timeout=20
            )
            if resp.ok:
                answer = resp.json().get("response", "").strip()
                for opt in options:
                    if opt.lower() in answer.lower() or answer.lower() in opt.lower():
                        return opt
        except:
            pass
        return None


# ═══════════════════════════════════════════════════════════════════
# SMART FILLER V3.2
# ═══════════════════════════════════════════════════════════════════

class SmartFillerV32:
    def __init__(self, headless: bool = False):
        self.headless = headless
        self.db = Database()
        self.profile = Profile()
        self.ai = TextAI()
        
        self.playwright = None
        self.browser = None
        self.page: Page = None
        
        self.fields: List[ProbedField] = []
        self.seen_selectors: Set[str] = set()
        self.stats = {"auto": 0, "user": 0, "learned": 0, "skipped": 0, "files": 0}
    
    # ───────────────────────────────────────────────────────────────
    # BROWSER
    # ───────────────────────────────────────────────────────────────
    
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
    
    # ───────────────────────────────────────────────────────────────
    # WAIT FOR JS / REACT
    # ───────────────────────────────────────────────────────────────
    
    def wait_for_stable(self, timeout: float = 2.0):
        """Wait until form is stable (no new elements appearing)."""
        prev_count = 0
        start = time.time()
        
        while time.time() - start < timeout:
            count = len(self.page.query_selector_all("input, select, textarea"))
            if count == prev_count:
                return  # Stable
            prev_count = count
            time.sleep(0.3)
    
    # ───────────────────────────────────────────────────────────────
    # HIGHLIGHT
    # ───────────────────────────────────────────────────────────────
    
    def highlight(self, selector: str, color: str = "red"):
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
        try:
            self.page.evaluate(f"""
                const el = document.querySelector('{selector}');
                if (el) el.style.outline = '';
            """)
        except:
            pass
    
    # ───────────────────────────────────────────────────────────────
    # PROBE FIELD
    # ───────────────────────────────────────────────────────────────
    
    def probe_field(self, el: ElementHandle, selector: str) -> Optional[ProbedField]:
        try:
            tag = el.evaluate("e => e.tagName.toLowerCase()")
            el_type = el.get_attribute("type") or "text"
            role = el.get_attribute("role") or ""
            aria_haspopup = el.get_attribute("aria-haspopup") or ""
            required = el.get_attribute("required") is not None or \
                       el.get_attribute("aria-required") == "true"
            
            if el_type in ("hidden", "submit", "button"):
                return None
            if not el.is_visible():
                return None
            
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
            
            current = ""
            try:
                if tag == "select":
                    current = el.evaluate("e => e.options[e.selectedIndex]?.text || ''")
                elif el_type == "checkbox":
                    current = "checked" if el.is_checked() else ""
                elif el_type != "file":
                    current = el.input_value() or ""
            except:
                pass
            
            is_dropdown = False
            options = []
            
            if tag == "select":
                is_dropdown = True
                options = el.evaluate("e => Array.from(e.options).map(o => o.text).filter(t => t && t !== 'Select...')")
            elif role == "combobox" or aria_haspopup in ("listbox", "true"):
                is_dropdown = True
                options = self._probe_combobox_options(el)
            
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
                is_editable=el.is_editable() if el_type != "file" else True,
                current_value=current
            )
        except:
            return None
    
    def _probe_combobox_options(self, el: ElementHandle) -> List[str]:
        options = []
        try:
            el.click()
            time.sleep(0.3)
            listbox = self.page.query_selector("[role='listbox'], .select__menu, [class*='menu']")
            if listbox:
                for opt in listbox.query_selector_all("[role='option'], .select__option, [class*='option']"):
                    text = opt.inner_text().strip()
                    if text and text not in ("Select...", ""):
                        options.append(text)
            self.page.keyboard.press("Escape")
            time.sleep(0.1)
        except:
            try:
                self.page.keyboard.press("Escape")
            except:
                pass
        return options[:20]
    
    # ───────────────────────────────────────────────────────────────
    # SCAN
    # ───────────────────────────────────────────────────────────────
    
    def scan(self) -> List[ProbedField]:
        """Scan and probe all NEW fields."""
        new_fields = []
        
        for el in self.page.query_selector_all("input, select, textarea"):
            el_id = el.get_attribute("id") or ""
            el_name = el.get_attribute("name") or ""
            
            if el_id:
                selector = f"#{el_id}"
            elif el_name:
                selector = f"[name='{el_name}']"
            else:
                continue
            
            # Skip already seen
            if selector in self.seen_selectors:
                continue
            
            field = self.probe_field(el, selector)
            if field:
                self.seen_selectors.add(selector)
                self.fields.append(field)
                new_fields.append(field)
        
        return new_fields
    
    # ───────────────────────────────────────────────────────────────
    # FILE UPLOAD
    # ───────────────────────────────────────────────────────────────
    
    def upload_file(self, field: ProbedField) -> bool:
        """Upload resume/cover letter."""
        label_lower = field.label.lower()
        
        # Determine which file to upload
        file_path = None
        if any(kw in label_lower for kw in ["resume", "cv", "attach"]):
            if RESUME_PATH.exists():
                file_path = RESUME_PATH
        # Could add cover letter here too
        
        if not file_path:
            return False
        
        try:
            el = self.page.query_selector(field.selector)
            if el:
                el.set_input_files(str(file_path))
                time.sleep(0.5)
                print(f"   📄 Uploaded: {file_path.name}")
                self.stats["files"] += 1
                return True
        except Exception as e:
            print(f"   ❌ Upload error: {e}")
        
        return False
    
    # ───────────────────────────────────────────────────────────────
    # FILL FIELD
    # ───────────────────────────────────────────────────────────────
    
    def fill_field(self, field: ProbedField, value: str) -> bool:
        try:
            el = self.page.query_selector(field.selector)
            if not el:
                return False
            
            el.scroll_into_view_if_needed()
            time.sleep(0.1)
            
            if field.is_dropdown:
                el.click()
                time.sleep(0.2)
                el.type(value[:15], delay=30)
                time.sleep(0.3)
                self.page.keyboard.press("ArrowDown")
                time.sleep(0.1)
                self.page.keyboard.press("Enter")
                time.sleep(0.2)
            elif field.field_type == FieldType.CHECKBOX:
                should_check = value.lower() in ("yes", "true", "1", "checked")
                if should_check != el.is_checked():
                    el.click()
            else:
                el.fill(value)
            
            field.filled = True
            return True
        except Exception as e:
            print(f"   ❌ Fill error: {e}")
            return False
    
    def read_field(self, field: ProbedField) -> str:
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
    
    # ───────────────────────────────────────────────────────────────
    # FIND ANSWER
    # ───────────────────────────────────────────────────────────────
    
    def find_answer(self, field: ProbedField) -> Tuple[Optional[str], str]:
        label = field.label
        
        if field.is_dropdown:
            saved = self.db.find_dropdown_choice(label)
            if saved:
                return saved, "database"
        else:
            saved = self.db.find_answer(label)
            if saved:
                return saved, "database"
        
        if not field.is_dropdown:
            profile_val = self.profile.find_for_label(label)
            if profile_val:
                return profile_val, "profile"
        
        if field.is_dropdown and field.options:
            has_yes = any("yes" in o.lower() for o in field.options)
            has_no = any("no" in o.lower() for o in field.options)
            
            if has_yes or has_no:
                default = self.profile.find_yes_no_default(label)
                if default:
                    for opt in field.options:
                        if default.lower() in opt.lower():
                            return opt, "default"
            
            demo_default = self.profile.find_demographic_default(label)
            if demo_default:
                for opt in field.options:
                    if demo_default.lower() in opt.lower():
                        return opt, "default"
        
        return None, ""
    
    # ───────────────────────────────────────────────────────────────
    # PROCESS
    # ───────────────────────────────────────────────────────────────
    
    def process_fields(self, fields: List[ProbedField], interactive: bool = True) -> List[ProbedField]:
        """Process a list of fields. Returns fields that need review."""
        new_fields = []
        
        for field in fields:
            # Handle FILE upload
            if field.field_type == FieldType.FILE:
                self.highlight(field.selector, "blue")
                if self.upload_file(field):
                    field.filled = True
                self.unhighlight(field.selector)
                continue
            
            # Skip already filled
            if field.current_value and field.current_value not in ("", "Select...", "Select"):
                continue
            
            answer, source = self.find_answer(field)
            
            if answer:
                self.highlight(field.selector, "green")
                self.fill_field(field, answer)
                self.unhighlight(field.selector)
                
                field.answer = answer
                field.answer_source = source
                self.stats["auto"] += 1
                
                display = f"({source})" if source != "database" else "(DB)"
                print(f"   ✅ {field.label[:40]:<40} = {answer[:20]} {display}")
            else:
                new_fields.append(field)
        
        return new_fields
    
    def interactive_review(self, fields: List[ProbedField]):
        """Interactive review of new fields."""
        if not fields:
            return
        
        print("\n" + "="*65)
        print(f"🎓 LEARNING MODE: {len(fields)} new fields")
        print("="*65)
        
        for field in fields:
            self.highlight(field.selector, "orange")
            
            print(f"\n📌 Field: \"{field.label[:60]}\"")
            print(f"   Type: {field.field_type.value}", end="")
            
            if field.is_dropdown and field.options:
                print(f" (dropdown with {len(field.options)} options)")
                for i, opt in enumerate(field.options[:10], 1):
                    print(f"      {i}. {opt}")
                if len(field.options) > 10:
                    print(f"      ... and {len(field.options)-10} more")
                
                if self.ai.available:
                    suggested = self.ai.choose_option(field.label, field.options, self.profile.get_context())
                    if suggested:
                        print(f"   💡 AI suggests: {suggested}")
                
                print(f"\n   Enter number (1-{len(field.options)}), or 's' to skip:")
                user = input("   > ").strip()
                
                if user.lower() == 's':
                    self.stats["skipped"] += 1
                elif user.isdigit() and 1 <= int(user) <= len(field.options):
                    choice = field.options[int(user)-1]
                    self.fill_field(field, choice)
                    self.db.save_dropdown_choice(field.label, choice)
                    self.stats["learned"] += 1
                    self.stats["user"] += 1
                else:
                    self.stats["skipped"] += 1
            else:
                print(" (text)")
                
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
                else:
                    final = self.read_field(field)
                    if final:
                        self.db.save_answer(field.label, final)
                        self.stats["learned"] += 1
                        self.stats["user"] += 1
                    else:
                        self.stats["skipped"] += 1
            
            self.unhighlight(field.selector)
    
    # ───────────────────────────────────────────────────────────────
    # MULTI-PASS
    # ───────────────────────────────────────────────────────────────
    
    def multi_pass_fill(self, interactive: bool = True, max_passes: int = 5):
        """Fill form with multiple passes to catch dynamic fields."""
        
        for pass_num in range(1, max_passes + 1):
            print(f"\n🔄 Pass {pass_num}...")
            
            # Wait for JS/React
            self.wait_for_stable()
            
            # Scan for new fields
            new_fields = self.scan()
            
            if not new_fields:
                print("   No new fields found.")
                break
            
            print(f"   Found {len(new_fields)} new fields")
            
            # Process them
            needs_review = self.process_fields(new_fields, interactive)
            
            # Interactive review for unknown fields
            if interactive and needs_review:
                self.interactive_review(needs_review)
            
            # Wait for any dynamic updates
            time.sleep(0.5)
        
        # Final verification
        self.final_verify()
    
    # ───────────────────────────────────────────────────────────────
    # FINAL VERIFICATION
    # ───────────────────────────────────────────────────────────────
    
    def final_verify(self):
        """Final verification report."""
        print("\n" + "="*65)
        print("📋 FINAL VERIFICATION")
        print("="*65)
        
        empty = []
        filled = []
        errors = []
        
        for field in self.fields:
            if field.field_type == FieldType.FILE:
                if field.filled:
                    filled.append(f"📄 {field.label[:40]}")
                else:
                    empty.append(f"📄 {field.label[:40]} (no file)")
                continue
            
            value = self.read_field(field)
            
            if value and value not in ("Select...", "Select", ""):
                filled.append(f"{field.label[:40]}: {value[:20]}")
            else:
                empty.append(field.label[:50])
            
            # Check for validation errors
            try:
                el = self.page.query_selector(field.selector)
                if el and el.get_attribute("aria-invalid") == "true":
                    errors.append(field.label[:50])
            except:
                pass
        
        print(f"\n✅ Filled ({len(filled)}):")
        for f in filled[:15]:
            print(f"   {f}")
        if len(filled) > 15:
            print(f"   ... and {len(filled)-15} more")
        
        if empty:
            print(f"\n❌ Empty ({len(empty)}):")
            for e in empty:
                print(f"   {e}")
        
        if errors:
            print(f"\n⚠️ Validation Errors ({len(errors)}):")
            for e in errors:
                print(f"   {e}")
        
        print("\n" + "="*65)
        print("📊 SUMMARY")
        print("="*65)
        print(f"   Auto-filled:  {self.stats['auto']}")
        print(f"   User filled:  {self.stats['user']}")
        print(f"   Files:        {self.stats['files']}")
        print(f"   Learned:      {self.stats['learned']}")
        print(f"   Skipped:      {self.stats['skipped']}")
        print("="*65)
    
    # ───────────────────────────────────────────────────────────────
    # MAIN
    # ───────────────────────────────────────────────────────────────
    
    def run(self, url: str, interactive: bool = True):
        try:
            self.start()
            self.goto(url)
            self.multi_pass_fill(interactive)
            
            if not self.headless:
                print("\n👀 Review form in browser. Submit when ready.")
                print("   Press ENTER to close browser...")
                input()
        finally:
            self.stop()


# ═══════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════

def main():
    import sys
    
    url = sys.argv[1] if len(sys.argv) > 1 else \
        "https://job-boards.greenhouse.io/embed/job_app?token=7404427&for=coinbase&gh_jid=7404427"
    
    print("\n" + "="*65)
    print("🚀 SMART FILLER V3.2")
    print("   - File Upload")
    print("   - Multi-pass (dynamic fields)")
    print("   - Wait for JS/React")
    print("   - Final Verification")
    print("="*65)
    
    filler = SmartFillerV32(headless=False)
    filler.run(url, interactive=True)


if __name__ == "__main__":
    main()
