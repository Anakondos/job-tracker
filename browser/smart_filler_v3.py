"""
Smart Filler V3 - Interactive Learning Mode

Features:
- Fills known fields automatically
- For NEW fields: AI suggests → User reviews → System learns
- User sees browser, can edit in real-time
- Learns from user's final input (not just AI suggestion)
- No Vision needed - all text based!
"""

import json
import re
import time
import requests
from pathlib import Path
from typing import Optional, Dict, List, Tuple
from dataclasses import dataclass, field as dataclass_field

from playwright.sync_api import sync_playwright


# Paths
BROWSER_DIR = Path(__file__).parent
DATABASE_PATH = BROWSER_DIR / "learned_database.json"
PROFILE_PATH = BROWSER_DIR / "profiles" / "anton_tpm.json"


# ============================================================
# DATABASE
# ============================================================

class LearnedDatabase:
    """Self-learning database."""
    
    def __init__(self, path: Path = DATABASE_PATH):
        self.path = path
        self.data = self._load()
        self._new_learned = 0
    
    def _load(self) -> dict:
        if self.path.exists():
            with open(self.path, "r") as f:
                return json.load(f)
        return {
            "field_answers": {},      # label_key → answer
            "profile_mappings": {},   # label_key → profile.path
            "stats": {"total_fields": 0, "sessions": 0}
        }
    
    def save(self):
        self.data["stats"]["total_fields"] = len(self.data["field_answers"])
        with open(self.path, "w") as f:
            json.dump(self.data, f, indent=2, ensure_ascii=False)
    
    def _normalize_label(self, label: str) -> str:
        """Normalize label for matching."""
        # Lowercase, remove special chars, collapse spaces
        key = label.lower().strip()
        key = re.sub(r'[*?!:\-_()]+', ' ', key)
        key = re.sub(r'\s+', ' ', key).strip()
        return key
    
    def find_answer(self, label: str) -> Optional[str]:
        """Find answer in database."""
        key = self._normalize_label(label)
        
        # Exact match
        if key in self.data["field_answers"]:
            return self.data["field_answers"][key]
        
        # Partial match (label contains or is contained)
        for stored_key, answer in self.data["field_answers"].items():
            if stored_key in key or key in stored_key:
                return answer
        
        return None
    
    def find_profile_mapping(self, label: str) -> Optional[str]:
        """Find profile path mapping."""
        key = self._normalize_label(label)
        
        for stored_key, path in self.data["profile_mappings"].items():
            if stored_key in key or key in stored_key:
                return path
        
        return None
    
    def learn(self, label: str, answer: str, profile_path: str = None):
        """Learn new field → answer mapping."""
        key = self._normalize_label(label)
        
        self.data["field_answers"][key] = answer
        
        if profile_path:
            self.data["profile_mappings"][key] = profile_path
        
        self._new_learned += 1
        self.save()
        
        print(f"   💾 Saved: '{label[:40]}' → '{answer[:30]}...'")
    
    def get_stats(self) -> dict:
        return {
            "total_fields": len(self.data["field_answers"]),
            "new_this_session": self._new_learned
        }


# ============================================================
# TEXT AI (no vision!)
# ============================================================

class TextAI:
    """Text AI for generating answers."""
    
    def __init__(self, model: str = "llama3.2:3b", url: str = "http://localhost:11434"):
        self.model = model
        self.url = url
        self.available = self._check_available()
    
    def _check_available(self) -> bool:
        try:
            resp = requests.get(f"{self.url}/api/tags", timeout=5)
            return resp.ok
        except:
            return False
    
    def generate(self, question: str, context: str) -> str:
        """Generate answer for question."""
        if not self.available:
            return ""
        
        prompt = f"""Answer this job application question based on the profile.

Question: {question}

Profile:
{context}

Write a professional answer in 1-3 sentences. Be specific and direct.

Answer:"""

        try:
            resp = requests.post(
                f"{self.url}/api/generate",
                json={
                    "model": self.model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"temperature": 0.3, "num_predict": 150}
                },
                timeout=30
            )
            
            if resp.ok:
                answer = resp.json().get("response", "").strip()
                # Clean up
                for prefix in ["Answer:", "Response:", "A:"]:
                    if answer.startswith(prefix):
                        answer = answer[len(prefix):].strip()
                return answer
                
        except Exception as e:
            print(f"   ⚠️ AI error: {e}")
        
        return ""


# ============================================================
# PROFILE
# ============================================================

class Profile:
    """User profile data."""
    
    # Common label → profile path mappings
    KNOWN_MAPPINGS = {
        "first name": "personal.first_name",
        "last name": "personal.last_name",
        "full name": "personal.full_name",
        "email": "personal.email",
        "phone": "personal.phone",
        "location": "personal.location",
        "city": "personal.city",
        "country": "personal.country",
        "linkedin": "links.linkedin",
        "github": "links.github",
        "portfolio": "links.portfolio",
        "website": "links.website",
        "company name": "work_experience.0.company",
        "employer": "work_experience.0.company",
        "job title": "work_experience.0.title",
        "title": "work_experience.0.title",
        "position": "work_experience.0.title",
        "start date month": "work_experience.0.start_month",
        "start month": "work_experience.0.start_month",
        "start date year": "work_experience.0.start_year",
        "start year": "work_experience.0.start_year",
        "school": "education.0.school",
        "university": "education.0.school",
        "degree": "education.0.degree",
        "discipline": "education.0.discipline",
        "major": "education.0.discipline",
        "field of study": "education.0.discipline",
    }
    
    # Common yes/no answers
    YES_NO_DEFAULTS = {
        "18 years": "Yes",
        "authorized to work": "Yes",
        "legally authorized": "Yes",
        "eligible to work": "Yes",
        "sponsorship": "No",
        "require sponsorship": "No",
        "visa sponsorship": "No",
        "previously employed": "No",
        "former employee": "No",
        "referred": "No",
        "confirm receipt": "Yes",
        "acknowledge": "Yes",
        "agree": "Yes",
    }
    
    # Demographic defaults
    DEMOGRAPHIC_DEFAULTS = {
        "gender": "Decline to self-identify",
        "race": "Decline to self-identify",
        "ethnicity": "Decline to self-identify",
        "hispanic": "Decline to self-identify",
        "latino": "Decline to self-identify",
        "veteran": "I am not a protected veteran",
        "disability": "I do not want to answer",
    }
    
    def __init__(self, path: Path = PROFILE_PATH):
        self.data = self._load(path)
    
    def _load(self, path: Path) -> dict:
        if path.exists():
            with open(path, "r") as f:
                return json.load(f)
        return {}
    
    def get_value(self, path: str) -> str:
        """Get value by dot path."""
        parts = path.split(".")
        value = self.data
        
        for part in parts:
            if value is None:
                return ""
            if part.isdigit():
                idx = int(part)
                if isinstance(value, list) and idx < len(value):
                    value = value[idx]
                else:
                    return ""
            elif isinstance(value, dict):
                value = value.get(part)
            else:
                return ""
        
        if isinstance(value, bool):
            return "Yes" if value else "No"
        return str(value) if value else ""
    
    def find_value_for_label(self, label: str) -> Tuple[Optional[str], Optional[str]]:
        """
        Find profile value for a label.
        Returns: (value, profile_path) or (None, None)
        """
        label_lower = label.lower()
        
        # Check known mappings
        for pattern, path in self.KNOWN_MAPPINGS.items():
            if pattern in label_lower:
                value = self.get_value(path)
                if value:
                    return value, path
        
        # Check yes/no defaults
        for pattern, answer in self.YES_NO_DEFAULTS.items():
            if pattern in label_lower:
                return answer, None
        
        # Check demographic defaults
        for pattern, answer in self.DEMOGRAPHIC_DEFAULTS.items():
            if pattern in label_lower:
                return answer, None
        
        return None, None
    
    def get_context_for_ai(self) -> str:
        """Get profile summary for AI context."""
        parts = []
        
        personal = self.data.get("personal", {})
        parts.append(f"Name: {personal.get('first_name', '')} {personal.get('last_name', '')}")
        parts.append(f"Email: {personal.get('email', '')}")
        parts.append(f"Location: {personal.get('location', '')}")
        
        work = self.data.get("work_experience", [])
        if work:
            w = work[0]
            parts.append(f"Current role: {w.get('title', '')} at {w.get('company', '')}")
        
        edu = self.data.get("education", [])
        if edu:
            e = edu[0]
            parts.append(f"Education: {e.get('degree', '')} in {e.get('discipline', '')} from {e.get('school', '')}")
        
        return "\n".join(parts)


# ============================================================
# FORM FIELD
# ============================================================

@dataclass
class FormField:
    """A form field."""
    selector: str
    label: str
    html_type: str          # input, select, textarea
    input_type: str         # text, email, checkbox, etc
    required: bool
    options: List[str]      # For select
    current_value: str = ""


# ============================================================
# SMART FILLER V3
# ============================================================

class SmartFillerV3:
    """Interactive self-learning form filler."""
    
    def __init__(self, headless: bool = False):
        self.headless = headless
        self.db = LearnedDatabase()
        self.profile = Profile()
        self.ai = TextAI()
        self.page = None
        self.browser = None
        self.playwright = None
        
        self.stats = {
            "auto_filled": 0,
            "user_reviewed": 0,
            "skipped": 0,
            "learned": 0
        }
    
    def start_browser(self):
        """Start browser."""
        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch(
            headless=self.headless,
            slow_mo=100 if not self.headless else 0
        )
        self.page = self.browser.new_page(viewport={"width": 1300, "height": 900})
    
    def close_browser(self):
        """Close browser."""
        if self.browser:
            self.browser.close()
        if self.playwright:
            self.playwright.stop()
    
    def open_url(self, url: str):
        """Open job application URL."""
        print(f"\n🌐 Opening: {url}")
        self.page.goto(url, wait_until="networkidle")
        time.sleep(2)
        print(f"📄 Page: {self.page.title()[:60]}")
    
    def scan_fields(self) -> List[FormField]:
        """Scan all form fields."""
        print("\n🔍 Scanning form...")
        fields = []
        
        elements = self.page.query_selector_all("input, select, textarea")
        
        for el in elements:
            try:
                el_id = el.get_attribute("id") or ""
                el_name = el.get_attribute("name") or ""
                el_type = el.get_attribute("type") or "text"
                tag = el.evaluate("el => el.tagName.toLowerCase()")
                placeholder = el.get_attribute("placeholder") or ""
                aria_label = el.get_attribute("aria-label") or ""
                required = el.get_attribute("required") is not None
                
                # Skip system fields
                if el_type in ("hidden", "submit", "button", "file"):
                    continue
                
                # Skip invisible
                if not el.is_visible():
                    continue
                
                # Build selector
                if el_id:
                    selector = f"#{el_id}"
                elif el_name:
                    selector = f"[name='{el_name}']"
                else:
                    continue
                
                # Find label
                label = ""
                if el_id:
                    label_el = self.page.query_selector(f"label[for='{el_id}']")
                    if label_el:
                        label = label_el.inner_text().strip()
                if not label:
                    label = aria_label or placeholder or el_name or el_id
                
                # Get current value
                current = ""
                try:
                    if tag == "select":
                        current = el.evaluate("el => el.options[el.selectedIndex]?.text || ''")
                    elif el_type == "checkbox":
                        current = "checked" if el.is_checked() else ""
                    else:
                        current = el.input_value() or ""
                except:
                    pass
                
                # Get options for select
                options = []
                if tag == "select":
                    try:
                        options = el.evaluate("el => Array.from(el.options).map(o => o.text)")
                    except:
                        pass
                
                fields.append(FormField(
                    selector=selector,
                    label=label,
                    html_type=tag,
                    input_type=el_type,
                    required=required,
                    options=options,
                    current_value=current
                ))
                
            except:
                continue
        
        print(f"   Found {len(fields)} fillable fields")
        return fields
    
    def fill_field(self, field: FormField, value: str) -> str:
        """
        Fill field and return actual value after fill.
        """
        try:
            el = self.page.query_selector(field.selector)
            if not el or not el.is_visible():
                return ""
            
            el.scroll_into_view_if_needed()
            time.sleep(0.1)
            
            # Fill based on type
            if field.html_type == "select":
                # Greenhouse-style dropdown
                el.click()
                time.sleep(0.2)
                el.type(value[:20], delay=30)
                time.sleep(0.3)
                self.page.keyboard.press("ArrowDown")
                time.sleep(0.1)
                self.page.keyboard.press("Enter")
                time.sleep(0.2)
                
            elif field.input_type == "checkbox":
                should_check = value.lower() in ("yes", "true", "1", "checked")
                is_checked = el.is_checked()
                if should_check != is_checked:
                    el.click()
                    
            else:
                el.fill(value)
            
            time.sleep(0.1)
            
            # Read back actual value
            if field.html_type == "select":
                return el.evaluate("el => el.options[el.selectedIndex]?.text || ''")
            elif field.input_type == "checkbox":
                return "checked" if el.is_checked() else ""
            else:
                return el.input_value() or ""
                
        except Exception as e:
            print(f"   ❌ Error: {e}")
            return ""
    
    def read_field_value(self, field: FormField) -> str:
        """Read current value of field."""
        try:
            el = self.page.query_selector(field.selector)
            if not el:
                return ""
            
            if field.html_type == "select":
                return el.evaluate("el => el.options[el.selectedIndex]?.text || ''")
            elif field.input_type == "checkbox":
                return "checked" if el.is_checked() else ""
            else:
                return el.input_value() or ""
        except:
            return ""
    
    def process_form(self, interactive: bool = True):
        """
        Process all fields in form.
        
        interactive=True: Ask user to review new fields
        interactive=False: Auto-fill everything, skip unknowns
        """
        fields = self.scan_fields()
        
        new_fields = []  # Fields that need user review
        
        print("\n📝 Processing fields...")
        
        for field in fields:
            # Skip already filled
            if field.current_value and field.current_value not in ("", "Select...", "Select"):
                continue
            
            label = field.label
            answer = None
            source = None
            is_new = False
            
            # 1. Check database first
            db_answer = self.db.find_answer(label)
            if db_answer:
                answer = db_answer
                source = "database"
            
            # 2. Check profile mappings
            if not answer:
                profile_answer, profile_path = self.profile.find_value_for_label(label)
                if profile_answer:
                    answer = profile_answer
                    source = "profile"
            
            # 3. If still no answer - it's new field
            if not answer:
                is_new = True
                
                # Try AI
                if self.ai.available:
                    ai_answer = self.ai.generate(label, self.profile.get_context_for_ai())
                    if ai_answer:
                        answer = ai_answer
                        source = "ai"
            
            # Fill the field
            if answer:
                actual = self.fill_field(field, answer)
                
                if is_new:
                    new_fields.append({
                        "field": field,
                        "suggested": answer,
                        "source": source
                    })
                    print(f"   🆕 {label[:40]:<40} = {answer[:25]}... (AI)")
                else:
                    self.stats["auto_filled"] += 1
                    print(f"   ✅ {label[:40]:<40} = {answer[:25]}...")
            else:
                new_fields.append({
                    "field": field,
                    "suggested": "",
                    "source": "none"
                })
                print(f"   ❓ {label[:40]:<40} = ??? (unknown)")
        
        # Interactive review of new fields
        if interactive and new_fields:
            print("\n" + "="*60)
            print(f"🎓 LEARNING MODE: {len(new_fields)} new fields to review")
            print("="*60)
            print("Check the browser window, edit fields if needed.")
            print("")
            
            for item in new_fields:
                field = item["field"]
                suggested = item["suggested"]
                
                print(f"\n📌 Field: \"{field.label}\"")
                if suggested:
                    print(f"   AI filled: \"{suggested[:50]}...\"")
                else:
                    print(f"   (empty - fill manually in browser)")
                
                print(f"   → Edit in browser if needed, then press ENTER")
                print(f"   → Or type 's' to skip this field")
                
                user_input = input("   > ").strip().lower()
                
                if user_input == 's':
                    self.stats["skipped"] += 1
                    print(f"   ⏭️  Skipped")
                    continue
                
                # Read what user left/changed
                final_value = self.read_field_value(field)
                
                if final_value:
                    # Learn from user's final answer
                    self.db.learn(field.label, final_value)
                    self.stats["learned"] += 1
                    self.stats["user_reviewed"] += 1
                    print(f"   💾 Learned: \"{final_value[:40]}...\"")
                else:
                    self.stats["skipped"] += 1
                    print(f"   ⏭️  Empty, skipped")
        
        # Summary
        print("\n" + "="*60)
        print("📊 SESSION SUMMARY")
        print("="*60)
        print(f"   Auto-filled:   {self.stats['auto_filled']}")
        print(f"   User reviewed: {self.stats['user_reviewed']}")
        print(f"   Learned:       {self.stats['learned']}")
        print(f"   Skipped:       {self.stats['skipped']}")
        print(f"   Total in DB:   {self.db.get_stats()['total_fields']}")
        print("="*60)
    
    def run(self, url: str, interactive: bool = True):
        """
        Main entry point.
        
        Args:
            url: Job application URL
            interactive: If True, ask user to review new fields
        """
        try:
            self.start_browser()
            self.open_url(url)
            self.process_form(interactive=interactive)
            
            if not self.headless:
                print("\n👀 Review the form in browser.")
                print("   Submit manually when ready.")
                print("   Press ENTER here to close browser...")
                input()
            
        finally:
            self.close_browser()


# ============================================================
# MAIN
# ============================================================

def main():
    """Run smart filler."""
    import sys
    
    url = sys.argv[1] if len(sys.argv) > 1 else \
        "https://job-boards.greenhouse.io/embed/job_app?token=7404427&for=coinbase&gh_jid=7404427"
    
    print("\n" + "="*60)
    print("🚀 SMART FILLER V3 - Interactive Learning")
    print("="*60)
    
    filler = SmartFillerV3(headless=False)  # Visible browser!
    filler.run(url, interactive=True)


if __name__ == "__main__":
    main()
