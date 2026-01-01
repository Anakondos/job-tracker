"""
Browser automation client for job applications.

Usage:
    from browser import BrowserClient
    
    with BrowserClient() as browser:
        browser.open_job_page("https://...")
        browser.screenshot("step1.png")
        browser.find_and_click_apply()
        fields = browser.get_form_fields()
        browser.fill_form({"name": "John", "email": "john@example.com"})
"""

from playwright.sync_api import sync_playwright, Page, Browser, BrowserContext
from pathlib import Path
import time
import json
from typing import Optional, Dict, List, Any

from .config import (
    SCREENSHOTS_DIR,
    PAGE_LOAD_TIMEOUT,
    CLOUDFLARE_WAIT,
    ELEMENT_TIMEOUT,
    USER_AGENT,
    BROWSER_ARGS,
    STEALTH_SCRIPT,
    APPLY_BUTTON_SELECTORS,
    FORM_FIELD_PATTERNS,
    AI_CONFIG,
)
from .profile import get_profile_manager, ProfileManager


class BrowserClient:
    """Browser automation client with Cloudflare bypass and AI fallback."""
    
    def __init__(self, headless: bool = False, use_chrome_profile: bool = False):
        """
        Initialize browser client.
        
        Args:
            headless: Run in headless mode (default False for Cloudflare bypass)
            use_chrome_profile: Use real Chrome profile with all cookies/logins
        """
        self.headless = headless
        self.use_chrome_profile = use_chrome_profile
        self.playwright = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self._screenshot_counter = 0
    
    def __enter__(self):
        self.start()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
    
    def start(self):
        """Start browser instance."""
        self.playwright = sync_playwright().start()
        
        if self.use_chrome_profile:
            # Use real Chrome with user profile (has all cookies/logins)
            # We copy cookies to a temp profile to avoid locking main Chrome
            import os
            import shutil
            import tempfile
            
            chrome_path = os.path.expanduser(
                "~/Library/Application Support/Google/Chrome"
            )
            
            # Create temp dir and copy essential files (cookies, login data)
            self.temp_profile_dir = tempfile.mkdtemp(prefix="chrome_profile_")
            print(f"🔐 Creating temp profile from Chrome cookies...")
            
            # Copy Default profile
            src_default = os.path.join(chrome_path, "Default")
            dst_default = os.path.join(self.temp_profile_dir, "Default")
            
            if os.path.exists(src_default):
                os.makedirs(dst_default, exist_ok=True)
                # Copy only essential files for login state
                for filename in ["Cookies", "Login Data", "Web Data", "Preferences"]:
                    src_file = os.path.join(src_default, filename)
                    if os.path.exists(src_file):
                        shutil.copy2(src_file, dst_default)
                print(f"  ✅ Copied cookies and login data")
            
            self.context = self.playwright.chromium.launch_persistent_context(
                user_data_dir=self.temp_profile_dir,
                headless=self.headless,
                viewport={"width": 1400, "height": 900},
                args=["--disable-blink-features=AutomationControlled"],
            )
            self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
            self.browser = None  # Not used with persistent context
        else:
            # Standard: clean browser
            self.browser = self.playwright.chromium.launch(
                headless=self.headless,
                args=BROWSER_ARGS,
            )
            self.context = self.browser.new_context(
                viewport={"width": 1400, "height": 900},
                user_agent=USER_AGENT,
            )
            self.page = self.context.new_page()
            
            # Add anti-detection script
            self.page.add_init_script(STEALTH_SCRIPT)
        
        print("✅ Browser started")
    
    def close(self):
        """Close browser and cleanup."""
        if self.browser:
            self.browser.close()
        if self.playwright:
            self.playwright.stop()
        print("✅ Browser closed")
    
    def open_job_page(self, url: str, wait_for_cloudflare: bool = True) -> bool:
        """
        Open a job page URL with Cloudflare bypass.
        
        Args:
            url: Job page URL
            wait_for_cloudflare: Wait for Cloudflare challenge to pass
            
        Returns:
            True if page loaded successfully
        """
        print(f"🌐 Opening: {url}")
        
        try:
            self.page.goto(url, wait_until="domcontentloaded", timeout=PAGE_LOAD_TIMEOUT * 1000)
            
            if wait_for_cloudflare:
                # Check if we're on Cloudflare challenge page
                title = self.page.title()
                if "Just a moment" in title or "Cloudflare" in title:
                    print("⏳ Waiting for Cloudflare...")
                    time.sleep(CLOUDFLARE_WAIT)
                    title = self.page.title()
            
            print(f"📄 Page title: {self.page.title()}")
            return True
            
        except Exception as e:
            print(f"❌ Error opening page: {e}")
            return False
    
    def screenshot(self, name: Optional[str] = None, full_page: bool = False) -> Path:
        """
        Take a screenshot.
        
        Args:
            name: Screenshot filename (auto-generated if None)
            full_page: Capture full page or just viewport
            
        Returns:
            Path to saved screenshot
        """
        if name is None:
            self._screenshot_counter += 1
            name = f"screenshot_{self._screenshot_counter}.png"
        
        if not name.endswith(".png"):
            name += ".png"
        
        path = SCREENSHOTS_DIR / name
        self.page.screenshot(path=str(path), full_page=full_page)
        print(f"📸 Screenshot saved: {path}")
        return path
    
    def find_apply_button(self) -> Optional[Any]:
        """
        Find the Apply button on the page.
        
        Returns:
            Element handle if found, None otherwise
        """
        print("🔍 Looking for Apply button...")
        
        for selector in APPLY_BUTTON_SELECTORS:
            try:
                element = self.page.query_selector(selector)
                if element and element.is_visible():
                    print(f"✅ Found Apply button: {selector}")
                    return element
            except Exception:
                continue
        
        print("❌ Apply button not found with standard selectors")
        return None
    
    def find_and_click_apply(self) -> bool:
        """
        Find and click the Apply button.
        
        Returns:
            True if successfully clicked
        """
        # First try to find the button
        button = self.find_apply_button()
        
        if button:
            button.scroll_into_view_if_needed()
            time.sleep(0.5)
            button.click()
            print("✅ Clicked Apply button")
            time.sleep(2)  # Wait for form/redirect
            return True
        
        # TODO: AI fallback to find non-standard buttons
        print("⚠️ Could not find Apply button - may need AI assistance")
        return False
    
    def scroll_down(self, pixels: int = 500):
        """Scroll page down by pixels."""
        self.page.evaluate(f"window.scrollBy(0, {pixels})")
        time.sleep(0.5)
    
    def scroll_to_bottom(self):
        """Scroll to bottom of page."""
        self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(0.5)
    
    def get_form_fields(self) -> List[Dict[str, Any]]:
        """
        Get all form fields on the page.
        
        Returns:
            List of field info dicts: {name, type, label, required, selector}
        """
        print("📋 Analyzing form fields...")
        
        fields = []
        
        # Find all input elements
        inputs = self.page.query_selector_all("input, textarea, select")
        
        for inp in inputs:
            try:
                field_info = {
                    "tag": inp.evaluate("el => el.tagName.toLowerCase()"),
                    "type": inp.get_attribute("type") or "text",
                    "name": inp.get_attribute("name") or "",
                    "id": inp.get_attribute("id") or "",
                    "placeholder": inp.get_attribute("placeholder") or "",
                    "required": inp.get_attribute("required") is not None,
                    "aria_label": inp.get_attribute("aria-label") or "",
                }
                
                # Try to find associated label
                field_id = field_info["id"]
                if field_id:
                    label = self.page.query_selector(f"label[for='{field_id}']")
                    if label:
                        field_info["label"] = label.inner_text()
                
                # Determine field purpose
                field_info["purpose"] = self._guess_field_purpose(field_info)
                
                fields.append(field_info)
                
            except Exception as e:
                continue
        
        print(f"✅ Found {len(fields)} form fields")
        return fields
    
    def _guess_field_purpose(self, field_info: Dict) -> str:
        """Guess the purpose of a form field based on its attributes."""
        # Combine all text attributes
        text = " ".join([
            field_info.get("name", ""),
            field_info.get("id", ""),
            field_info.get("placeholder", ""),
            field_info.get("aria_label", ""),
            field_info.get("label", ""),
        ]).lower()
        
        for purpose, patterns in FORM_FIELD_PATTERNS.items():
            for pattern in patterns:
                if pattern in text:
                    return purpose
        
        return "unknown"
    
    def fill_field(self, selector: str, value: str):
        """Fill a single form field."""
        try:
            field = self.page.query_selector(selector)
            if field:
                field.fill(value)
                print(f"✅ Filled field: {selector}")
                return True
        except Exception as e:
            print(f"❌ Error filling {selector}: {e}")
        return False
    
    def fill_form(self, data: Dict[str, str], fields: Optional[List[Dict]] = None):
        """
        Fill form fields with provided data.
        
        Args:
            data: Dict mapping purpose to value, e.g. {"name": "John", "email": "..."}
            fields: Optional pre-fetched fields list
        """
        if fields is None:
            fields = self.get_form_fields()
        
        filled = 0
        for field in fields:
            purpose = field.get("purpose", "unknown")
            if purpose in data:
                # Build selector
                selector = None
                if field.get("id"):
                    selector = f"#{field['id']}"
                elif field.get("name"):
                    selector = f"[name='{field['name']}']"
                
                if selector and self.fill_field(selector, data[purpose]):
                    filled += 1
        
        print(f"✅ Filled {filled}/{len(data)} fields")
        return filled
    
    def fill_form_from_profile(self, profile: Optional[ProfileManager] = None) -> int:
        """
        Automatically fill form using profile data with smart field matching.
        
        Args:
            profile: ProfileManager instance (uses default if None)
            
        Returns:
            Number of fields filled
        """
        if profile is None:
            profile = get_profile_manager()
        
        print("📝 Auto-filling form from profile...")
        
        fields = self.get_form_fields()
        filled = 0
        skipped = []
        
        for field in fields:
            field_name = field.get("name", "")
            field_id = field.get("id", "")
            placeholder = field.get("placeholder", "")
            label = field.get("label", "")
            field_type = field.get("type", "text")
            
            # Skip file inputs and hidden fields
            if field_type in ("file", "hidden", "submit", "button"):
                continue
            
            # Try to find matching value from profile
            value = profile.get_value_for_field(field_name, field_id, placeholder, label)
            
            if value:
                selector = None
                if field_id:
                    selector = f"#{field_id}"
                elif field_name:
                    selector = f"[name='{field_name}']"
                
                if selector:
                    try:
                        el = self.page.query_selector(selector)
                        if el and el.is_visible():
                            el.fill(str(value))
                            filled += 1
                            print(f"  ✅ {field_name or field_id}: {value[:30]}{'...' if len(str(value)) > 30 else ''}")
                    except Exception as e:
                        skipped.append(field_name or field_id)
            else:
                if field_name and field_name not in ("g-recaptcha-response",):
                    skipped.append(field_name or field_id)
        
        print(f"\n✅ Filled {filled} fields")
        if skipped:
            print(f"⚠️ Skipped {len(skipped)} fields (no matching data)")
        
        return filled
    
    def _fill_greenhouse_dropdown(self, selector: str, value: str) -> bool:
        """
        Fill a Greenhouse autocomplete dropdown.
        
        Greenhouse uses custom dropdowns that need click -> type -> select.
        """
        try:
            el = self.page.query_selector(selector)
            if el and el.is_visible():
                el.click()
                time.sleep(0.3)
                # Use type() for better compatibility with autocomplete
                el.fill("")  # Clear first
                el.type(value, delay=50)  # Type with delay for autocomplete
                time.sleep(0.5)  # Wait for autocomplete options
                # Select first option with ArrowDown + Enter
                self.page.keyboard.press("ArrowDown")
                time.sleep(0.2)
                self.page.keyboard.press("Enter")
                time.sleep(0.3)
                return True
        except Exception as e:
            pass
        return False
    
    def fill_greenhouse_form(self, profile: Optional[ProfileManager] = None) -> Dict[str, Any]:
        """
        Fill a Greenhouse application form.
        
        Handles Greenhouse-specific field structure.
        
        Returns:
            Dict with results: {filled: N, skipped: [], needs_attention: []}
        """
        if profile is None:
            profile = get_profile_manager()
        
        print("🌿 Filling Greenhouse form...")
        
        result = {
            "filled": 0,
            "skipped": [],
            "needs_attention": [],  # Fields that need manual input or AI
        }
        
        # Standard Greenhouse field mappings
        greenhouse_fields = {
            "#first_name": profile.get("personal.first_name"),
            "#last_name": profile.get("personal.last_name"),
            "#email": profile.get("personal.email"),
            "#phone": profile.get("personal.phone"),
        }
        
        # Fill standard fields
        for selector, value in greenhouse_fields.items():
            if value:
                try:
                    el = self.page.query_selector(selector)
                    if el and el.is_visible():
                        el.fill(str(value))
                        result["filled"] += 1
                        print(f"  ✅ {selector}: {value}")
                except Exception as e:
                    result["skipped"].append(selector)
        
        # Country dropdown (react-select)
        country = profile.get("personal.country", "United States")
        country_labels = self.page.query_selector_all("label")
        for label in country_labels:
            try:
                if "country" in label.inner_text().lower():
                    parent = label.evaluate_handle("el => el.parentElement")
                    dropdown = parent.as_element().query_selector("[class*='select'], [class*='dropdown'], select, input")
                    if dropdown and dropdown.is_visible():
                        dropdown.click()
                        time.sleep(0.3)
                        # Type more specific text to avoid UAE
                        self.page.keyboard.type("United Stat", delay=30)
                        time.sleep(0.8)
                        self.page.keyboard.press("Enter")
                        result["filled"] += 1
                        print(f"  ✅ Country: {country}")
                        break
            except:
                pass
        
        # Location field - might be Google Places autocomplete
        location = profile.get("personal.location")
        if location:
            loc_input = self.page.query_selector("#candidate-location")
            if loc_input and loc_input.is_visible():
                try:
                    loc_input.click()
                    time.sleep(0.2)
                    loc_input.type(location, delay=80)  # Slow for autocomplete
                    time.sleep(1.5)  # Wait for Google Places suggestions
                    self.page.keyboard.press("ArrowDown")
                    time.sleep(0.2)
                    self.page.keyboard.press("Enter")
                    time.sleep(0.3)
                    result["filled"] += 1
                    print(f"  ✅ Location: {location}")
                except:
                    result["skipped"].append("location")
        
        # Handle LinkedIn (often a custom question)
        linkedin = profile.get("links.linkedin")
        if linkedin:
            linkedin_fields = self.page.query_selector_all("input[name*='linkedin'], input[id*='linkedin']")
            for field in linkedin_fields:
                try:
                    if field.is_visible():
                        field.fill(linkedin)
                        result["filled"] += 1
                        print(f"  ✅ LinkedIn: {linkedin}")
                        break
                except:
                    pass
        
        # Check for custom questions that need attention
        custom_questions = self.page.query_selector_all("input[name^='question_'], textarea[name^='question_']")
        for q in custom_questions:
            try:
                name = q.get_attribute("name")
                # Try to find label
                label = ""
                label_el = self.page.query_selector(f"label[for='{q.get_attribute('id')}']")
                if label_el:
                    label = label_el.inner_text()
                
                # Try to match with common answers
                if label:
                    answer = profile.get_common_answer(label)
                    if answer and q.is_visible():
                        q.fill(answer)
                        result["filled"] += 1
                        print(f"  ✅ Question: {label[:40]}...")
                    else:
                        result["needs_attention"].append({"name": name, "label": label})
            except:
                pass
        
        # Report needs attention
        if result["needs_attention"]:
            print(f"\n⚠️ {len(result['needs_attention'])} questions need attention:")
            for q in result["needs_attention"][:5]:
                print(f"  - {q.get('label', q.get('name'))}")
        
        print(f"\n✅ Filled {result['filled']} fields total")
        return result
    
    def fill_greenhouse_work_experience(self, profile: Optional[ProfileManager] = None, index: int = 0) -> int:
        """
        Fill Work Experience section in Greenhouse form.
        
        Args:
            profile: ProfileManager instance
            index: Work experience entry index (0 for first job)
            
        Returns:
            Number of fields filled
        """
        if profile is None:
            profile = get_profile_manager()
        
        work_exp = profile.get("work_experience", [])
        if not work_exp or index >= len(work_exp):
            print("⚠️ No work experience in profile")
            return 0
        
        work = work_exp[index]
        filled = 0
        
        print(f"\n💼 Filling Work Experience #{index}...")
        
        # Company name
        if work.get("company"):
            el = self.page.query_selector(f"#company-name-{index}, input[id*='company-name-{index}']")
            if el and el.is_visible():
                el.fill(work["company"])
                filled += 1
                print(f"  ✅ Company: {work['company']}")
        
        # Title
        if work.get("title"):
            el = self.page.query_selector(f"#title-{index}, input[id*='title-{index}']")
            if el and el.is_visible():
                el.fill(work["title"])
                filled += 1
                print(f"  ✅ Title: {work['title']}")
        
        # Start date (dropdowns)
        if work.get("start_month"):
            if self._fill_greenhouse_dropdown(f"#start-date-month-{index}", work["start_month"]):
                filled += 1
                print(f"  ✅ Start Month: {work['start_month']}")
        
        if work.get("start_year"):
            if self._fill_greenhouse_dropdown(f"#start-date-year-{index}", work["start_year"]):
                filled += 1
                print(f"  ✅ Start Year: {work['start_year']}")
        
        # Current role checkbox - click on label
        if work.get("current"):
            current_label = self.page.query_selector(f"label:has-text('Current role')")
            if current_label:
                # Check if not already checked by looking at sibling checkbox
                checkbox = self.page.query_selector(f"input[type='checkbox'][id*='current-role-{index}']")
                if checkbox:
                    try:
                        is_checked = checkbox.is_checked()
                        if not is_checked:
                            current_label.click()
                            filled += 1
                            print("  ✅ Current role: checked")
                    except:
                        # Fallback: just click the label
                        current_label.click()
                        filled += 1
                        print("  ✅ Current role: checked")
        else:
            # End date (if not current)
            if work.get("end_month"):
                if self._fill_greenhouse_dropdown(f"#end-date-month-{index}", work["end_month"]):
                    filled += 1
                    print(f"  ✅ End Month: {work['end_month']}")
            
            if work.get("end_year"):
                if self._fill_greenhouse_dropdown(f"#end-date-year-{index}", work["end_year"]):
                    filled += 1
                    print(f"  ✅ End Year: {work['end_year']}")
        
        return filled
    
    def fill_greenhouse_education(self, profile: Optional[ProfileManager] = None, index: int = 0) -> int:
        """
        Fill Education section in Greenhouse form.
        
        Args:
            profile: ProfileManager instance  
            index: Education entry index (0 for first)
            
        Returns:
            Number of fields filled
        """
        if profile is None:
            profile = get_profile_manager()
        
        education = profile.get("education", [])
        if not education or index >= len(education):
            print("⚠️ No education in profile")
            return 0
        
        edu = education[index]
        filled = 0
        
        print(f"\n🎓 Filling Education #{index}...")
        
        # School (dropdown/autocomplete)
        if edu.get("school"):
            if self._fill_greenhouse_dropdown(f"#school--{index}", edu["school"]):
                filled += 1
                print(f"  ✅ School: {edu['school']}")
        
        # Degree (dropdown)
        if edu.get("degree"):
            if self._fill_greenhouse_dropdown(f"#degree--{index}", edu["degree"]):
                filled += 1
                print(f"  ✅ Degree: {edu['degree']}")
        
        # Discipline/Major (dropdown)
        if edu.get("discipline"):
            if self._fill_greenhouse_dropdown(f"#discipline--{index}", edu["discipline"]):
                filled += 1
                print(f"  ✅ Discipline: {edu['discipline']}")
        
        return filled
    
    def upload_greenhouse_resume(self, file_path: str) -> bool:
        """
        Upload resume to Greenhouse form.
        First removes any auto-filled resume, then uploads our file.
        
        Args:
            file_path: Path to resume file
            
        Returns:
            True if successful
        """
        print(f"\n📎 Uploading resume: {file_path}")
        
        try:
            # First, remove any auto-filled resume (click X button)
            remove_buttons = self.page.query_selector_all("button[aria-label*='Remove'], button[title*='Remove'], [class*='remove'], [class*='delete']")
            for btn in remove_buttons:
                try:
                    # Check if it's near Resume/CV section
                    parent_text = btn.evaluate("el => el.closest('div')?.innerText || ''")
                    if 'resume' in parent_text.lower() or 'cv' in parent_text.lower() or '.pdf' in parent_text.lower():
                        btn.click()
                        time.sleep(0.5)
                        print("  🗑️ Removed auto-filled resume")
                        break
                except:
                    pass
            
            # Also try clicking × near filename
            close_icons = self.page.query_selector_all("svg[class*='close'], span:has-text('×'), button:has-text('×')")
            for icon in close_icons:
                try:
                    parent_text = icon.evaluate("el => el.closest('div')?.innerText || ''")
                    if '.pdf' in parent_text.lower() or 'resume' in parent_text.lower():
                        icon.click()
                        time.sleep(0.5)
                        print("  🗑️ Removed auto-filled resume")
                        break
                except:
                    pass
            
            time.sleep(0.3)
            
            # Now upload our file
            with self.page.expect_file_chooser() as fc_info:
                # Click first Attach button (Resume)
                attach_buttons = self.page.query_selector_all("button:has-text('Attach')")
                if attach_buttons:
                    attach_buttons[0].click()
            
            file_chooser = fc_info.value
            file_chooser.set_files(file_path)
            print(f"  ✅ Resume uploaded: {file_path.split('/')[-1]}")
            return True
        except Exception as e:
            print(f"  ❌ Failed to upload resume: {e}")
            return False
    
    def fill_greenhouse_complete(self, profile: Optional[ProfileManager] = None) -> Dict[str, Any]:
        """
        Complete Greenhouse form filling including all sections.
        
        Returns:
            Dict with results
        """
        if profile is None:
            profile = get_profile_manager()
        
        result = {
            "basic_fields": 0,
            "resume": False,
            "work_experience": 0,
            "education": 0,
            "custom_questions": 0,
            "demographics": 0,
            "total": 0,
            "needs_attention": [],
        }
        
        # 1. Basic fields
        basic_result = self.fill_greenhouse_form(profile)
        result["basic_fields"] = basic_result["filled"]
        result["needs_attention"].extend(basic_result.get("needs_attention", []))
        
        # 2. Resume
        resume_path = profile.get("files.resume_path")
        if resume_path:
            result["resume"] = self.upload_greenhouse_resume(resume_path)
        
        # 3. Work Experience
        work_exp = profile.get("work_experience", [])
        for i in range(len(work_exp)):
            result["work_experience"] += self.fill_greenhouse_work_experience(profile, i)
        
        # 4. Education
        education = profile.get("education", [])
        for i in range(len(education)):
            result["education"] += self.fill_greenhouse_education(profile, i)
        
        # 5. Custom Questions (scroll down first)
        self.scroll_down(600)
        time.sleep(0.3)
        result["custom_questions"] = self.fill_greenhouse_custom_questions(profile)
        
        # 6. More custom questions (scroll more to reach all)
        self.scroll_down(600)
        time.sleep(0.3)
        result["custom_questions"] += self.fill_greenhouse_custom_questions(profile)
        
        # 7. Demographics (scroll to bottom where they usually are)
        self.scroll_to_bottom()
        time.sleep(0.5)
        result["demographics"] = self.fill_greenhouse_demographics(profile)
        
        result["total"] = (
            result["basic_fields"] + 
            (1 if result["resume"] else 0) + 
            result["work_experience"] + 
            result["education"] +
            result["custom_questions"] +
            result["demographics"]
        )
        
        # Scroll back to top
        self.page.evaluate("window.scrollTo(0, 0)")
        
        print(f"\n{'='*50}")
        print(f"📊 Form filling complete:")
        print(f"   Basic fields: {result['basic_fields']}")
        print(f"   Resume: {'✅' if result['resume'] else '❌'}")
        print(f"   Work experience: {result['work_experience']}")
        print(f"   Education: {result['education']}")
        print(f"   Custom questions: {result['custom_questions']}")
        print(f"   Demographics: {result['demographics']}")
        print(f"   TOTAL: {result['total']} fields")
        print(f"{'='*50}")
        
        # 8. AI Verification - check if any fields were missed
        try:
            from utils.ollama_ai import verify_form_fields, suggest_form_fixes, is_ollama_available
            
            if is_ollama_available():
                print("\n🤖 AI Verification...")
                
                # Get page HTML for AI analysis
                page_html = self.page.content()
                
                # Expected fields to verify
                expected = {
                    "first_name": profile.get("personal.first_name"),
                    "last_name": profile.get("personal.last_name"),
                    "email": profile.get("personal.email"),
                    "phone": profile.get("personal.phone"),
                    "country": profile.get("personal.country", "United States"),
                    "gender": profile.get("demographics.gender", "Male"),
                    "hispanic": profile.get("demographics.hispanic_latino", "No"),
                    "race": profile.get("demographics.race_ethnicity", "White"),
                    "veteran": profile.get("demographics.veteran_status"),
                    "disability": profile.get("demographics.disability_status"),
                }
                
                verification = verify_form_fields(page_html, expected)
                
                if verification.get("ok"):
                    print("   ✅ All fields verified!")
                else:
                    # Found issues - try to fix them
                    missing = verification.get("missing", [])
                    incorrect = verification.get("incorrect", [])
                    
                    if missing:
                        print(f"   ⚠️ Missing fields: {[m.get('field') for m in missing]}")
                    if incorrect:
                        print(f"   ⚠️ Incorrect fields: {[i.get('field') for i in incorrect]}")
                    
                    # Get fix suggestions from AI
                    profile_data = {
                        "personal": profile.profile.get("personal", {}),
                        "demographics": profile.profile.get("demographics", {}),
                    }
                    fixes = suggest_form_fixes(page_html, profile_data)
                    
                    if fixes:
                        print(f"   🔧 Attempting {len(fixes)} fixes...")
                        for fix in fixes[:5]:  # Limit to 5 fixes
                            try:
                                selector = fix.get("selector")
                                value = fix.get("value")
                                action = fix.get("action", "fill")
                                
                                if selector and value:
                                    el = self.page.query_selector(selector)
                                    if el and el.is_visible():
                                        if action == "select":
                                            el.click()
                                            time.sleep(0.3)
                                            self.page.keyboard.type(value, delay=30)
                                            time.sleep(0.5)
                                            self.page.keyboard.press("Enter")
                                        else:
                                            el.fill(value)
                                        print(f"      ✅ Fixed: {fix.get('field')}")
                                        result["total"] += 1
                            except Exception as e:
                                print(f"      ❌ Fix failed: {fix.get('field')} - {e}")
                    
                    result["needs_attention"].extend(verification.get("suggestions", []))
            else:
                print("\n⚠️ Ollama not available - skipping AI verification")
        except Exception as e:
            print(f"\n⚠️ AI verification error: {e}")
        
        return result
    
    def fill_greenhouse_demographics(self, profile: Optional[ProfileManager] = None) -> int:
        """
        Fill Demographics section (Gender, Race, Veteran, Disability).
        
        These are usually optional/voluntary with "Decline to self-identify" options.
        """
        if profile is None:
            profile = get_profile_manager()
        
        print("\n📋 Filling Demographics (voluntary)...")
        filled = 0
        
        # Label text -> value to select (Race will be filled after Hispanic)
        demographics_map = {
            "gender": profile.get("demographics.gender", "Male"),
            "hispanic": profile.get("demographics.hispanic_latino", "No"),
            "veteran": profile.get("demographics.veteran_status", "I am not a protected veteran"),
            "disability": profile.get("demographics.disability_status", "No, I do not"),
        }
        
        filled_keys = set()  # Track what we've filled
        
        # Helper function to fill a dropdown by label
        def fill_dropdown_by_label(key, value):
            labels = self.page.query_selector_all("label")
            for label in labels:
                try:
                    label_text = label.inner_text().lower().strip()
                    if key in label_text:
                        # Find dropdown
                        dropdown = None
                        parent = label.evaluate_handle("el => el.parentElement")
                        if parent:
                            dropdown = parent.as_element().query_selector("[class*='select'], [class*='Select'], input[role='combobox']")
                        
                        if not dropdown:
                            grandparent = label.evaluate_handle("el => el.parentElement?.parentElement")
                            if grandparent:
                                dropdown = grandparent.as_element().query_selector("[class*='select'], [class*='Select'], input[role='combobox']")
                        
                        if dropdown and dropdown.is_visible():
                            dropdown.scroll_into_view_if_needed()
                            time.sleep(0.2)
                            dropdown.click()
                            time.sleep(0.3)
                            self.page.keyboard.type(value, delay=30)
                            time.sleep(0.5)
                            self.page.keyboard.press("Enter")
                            time.sleep(0.3)
                            print(f"  ✅ {key}: {value}")
                            return True
                        break
                except:
                    pass
            return False
        
        # Fill Gender first
        if fill_dropdown_by_label("gender", demographics_map["gender"]):
            filled += 1
        
        # Fill Hispanic - this will trigger Race field to appear
        if fill_dropdown_by_label("hispanic", demographics_map["hispanic"]):
            filled += 1
            # Wait for Race field to appear dynamically
            time.sleep(1.0)
            print("  ⏳ Waiting for Race field to appear...")
        
        # Now fill Race (it should have appeared after Hispanic)
        race_value = profile.get("demographics.race_ethnicity", "White")
        if fill_dropdown_by_label("race", race_value):
            filled += 1
        
        # Fill Veteran
        if fill_dropdown_by_label("veteran", demographics_map["veteran"]):
            filled += 1
        
        # Fill Disability
        if fill_dropdown_by_label("disability", demographics_map["disability"]):
            filled += 1
        
        return filled
    
    def fill_greenhouse_custom_questions(self, profile: Optional[ProfileManager] = None) -> int:
        """
        Fill common custom questions in Greenhouse forms.
        """
        if profile is None:
            profile = get_profile_manager()
        
        print("\n❓ Filling Custom Questions...")
        filled = 0
        
        # LinkedIn URL
        linkedin = profile.get("links.linkedin")
        if linkedin:
            for label in self.page.query_selector_all("label"):
                if "linkedin" in label.inner_text().lower():
                    label_for = label.get_attribute("for")
                    if label_for:
                        inp = self.page.query_selector(f"#{label_for}")
                        if inp:
                            try:
                                inp.fill(linkedin)
                                filled += 1
                                print(f"  ✅ LinkedIn: {linkedin}")
                            except:
                                pass
                    break
        
        # Common Yes/No and confirmation questions
        yes_no_questions = {
            "18 years": "Yes",
            "previously been employed": "No",
            "legally authorized": profile.get("work_authorization.authorized_us", "Yes"),
            "sponsorship": profile.get("work_authorization.requires_sponsorship", "No"),
            "government official": "No",
            "close relative": "No",
            "AI tools": "Yes",
            # Coinbase-specific and other confirmations
            "please confirm": "Confirmed",
            "confirm receipt": "Confirmed",
            "privacy notice": "Confirmed",
            "arbitration": "Confirmed",
            "data privacy": "Confirmed",
            "conflict of interest": "No",
            "referred to this position": "No",
            "senior leader": "No",
            # Stripe-specific
            "work remotely": "Yes",
            "whatsapp": "No",
            "opt-in": "No",
        }
        
        for question_text, answer in yes_no_questions.items():
            for label in self.page.query_selector_all("label"):
                label_text = label.inner_text().lower()
                if question_text.lower() in label_text:
                    label_for = label.get_attribute("for")
                    if label_for:
                        el = self.page.query_selector(f"#{label_for}")
                        if el:
                            try:
                                el.scroll_into_view_if_needed()
                                time.sleep(0.2)
                                el.click()
                                time.sleep(0.3)
                                el.type(answer, delay=50)  # Slower typing
                                time.sleep(0.5)  # Wait for dropdown to filter
                                self.page.keyboard.press("ArrowDown")
                                time.sleep(0.2)
                                self.page.keyboard.press("Enter")
                                time.sleep(0.3)
                                filled += 1
                                print(f"  ✅ {question_text[:25]}...: {answer}")
                            except:
                                pass
                    break
        
        # How did you hear
        how_heard = profile.get("common_answers.how_heard", "LinkedIn")
        for label in self.page.query_selector_all("label"):
            if "how did you hear" in label.inner_text().lower():
                label_for = label.get_attribute("for")
                if label_for:
                    el = self.page.query_selector(f"#{label_for}")
                    if el:
                        try:
                            el.click()
                            time.sleep(0.2)
                            el.type(how_heard, delay=30)
                            time.sleep(0.2)
                            self.page.keyboard.press("ArrowDown")
                            self.page.keyboard.press("Enter")
                            time.sleep(0.2)
                            filled += 1
                            print(f"  ✅ How did you hear: {how_heard}")
                        except:
                            pass
                break
        
        # Text input questions (Stripe-style: employer, title, school, degree)
        work_exp = profile.get("work_experience", [])
        education = profile.get("education", [])
        
        text_questions = {
            "current or previous employer": work_exp[0].get("company", "") if work_exp else "",
            "current or previous job title": work_exp[0].get("title", "") if work_exp else "",
            "most recent school": education[0].get("school", "") if education else "",
            "most recent degree": education[0].get("degree", "") if education else "",
            "current employer": work_exp[0].get("company", "") if work_exp else "",
            # Experience years questions
            "years of experience": "15",
            "how many years": "15",
            "product management": "15",
            "program management": "15",
        }
        
        for question_text, value in text_questions.items():
            if not value:
                continue
            for label in self.page.query_selector_all("label"):
                label_text = label.inner_text().lower()
                if question_text in label_text:
                    label_for = label.get_attribute("for")
                    if label_for:
                        inp = self.page.query_selector(f"#{label_for}")
                        if inp:
                            try:
                                # Check if it's a text input (not dropdown)
                                tag = inp.evaluate("el => el.tagName")
                                inp_type = inp.get_attribute("type") or ""
                                if tag == "INPUT" and inp_type in ["", "text"]:
                                    inp.fill(value)
                                    filled += 1
                                    print(f"  ✅ {question_text[:25]}...: {value}")
                            except:
                                pass
                    break
        
        # AI-powered answers for unknown questions
        try:
            from browser.ai_agent import answer_custom_question
            from utils.ollama_ai import is_ollama_available
            
            if is_ollama_available():
                print("  \n🤖 AI checking for unanswered questions...")
                
                # Find all unfilled required fields
                labels = self.page.query_selector_all("label")
                for label in labels:
                    try:
                        label_text = label.inner_text().strip()
                        if not label_text or len(label_text) < 10:
                            continue
                        
                        # Skip already handled questions
                        already_handled = any(q in label_text.lower() for q in [
                            "linkedin", "name", "email", "phone", "resume", "gender",
                            "race", "veteran", "disability", "hispanic"
                        ])
                        if already_handled:
                            continue
                        
                        # Check if field is empty
                        label_for = label.get_attribute("for")
                        if label_for:
                            el = self.page.query_selector(f"#{label_for}")
                            if el and el.is_visible():
                                current_val = el.get_attribute("value") or el.inner_text()
                                if current_val and len(current_val.strip()) > 0:
                                    continue  # Already filled
                                
                                # Ask AI for answer
                                profile_data = profile.profile if hasattr(profile, 'profile') else {}
                                ai_answer = answer_custom_question(label_text, profile_data)
                                
                                if ai_answer:
                                    el.scroll_into_view_if_needed()
                                    time.sleep(0.2)
                                    
                                    tag = el.evaluate("el => el.tagName")
                                    if tag == "INPUT":
                                        el.fill(ai_answer)
                                    else:
                                        el.click()
                                        time.sleep(0.2)
                                        self.page.keyboard.type(ai_answer, delay=30)
                                        time.sleep(0.3)
                                        self.page.keyboard.press("Enter")
                                    
                                    filled += 1
                                    print(f"  🤖 AI answered: {label_text[:40]}... = {ai_answer[:30]}...")
                    except Exception as e:
                        pass
        except ImportError:
            pass
        except Exception as e:
            print(f"  ⚠️ AI questions error: {e}")
        
        return filled
    
    def upload_file(self, selector: str, file_path: str) -> bool:
        """Upload a file to a file input."""
        try:
            file_input = self.page.query_selector(selector)
            if file_input:
                file_input.set_input_files(file_path)
                print(f"✅ Uploaded file: {file_path}")
                return True
        except Exception as e:
            print(f"❌ Error uploading file: {e}")
        return False
    
    def get_page_text(self) -> str:
        """Get all visible text from the page."""
        return self.page.inner_text("body")
    
    def get_page_html(self) -> str:
        """Get page HTML."""
        return self.page.content()
    
    def wait_for_selector(self, selector: str, timeout: int = None):
        """Wait for an element to appear."""
        timeout = timeout or ELEMENT_TIMEOUT
        self.page.wait_for_selector(selector, timeout=timeout * 1000)
    
    def click(self, selector: str):
        """Click an element by selector."""
        self.page.click(selector)
        time.sleep(0.5)
    
    # ============= AI FALLBACK METHODS =============
    
    def _ask_ai(self, prompt: str) -> Optional[str]:
        """
        Ask AI for help (Ollama first, then Claude if configured).
        
        Args:
            prompt: Question for AI
            
        Returns:
            AI response or None
        """
        provider = AI_CONFIG.get("provider", "ollama")
        
        if provider == "ollama":
            return self._ask_ollama(prompt)
        elif provider == "claude":
            return self._ask_claude(prompt)
        
        return None
    
    def _ask_ollama(self, prompt: str) -> Optional[str]:
        """Ask local Ollama for help."""
        try:
            import requests
            response = requests.post(
                f"{AI_CONFIG['ollama_url']}/api/generate",
                json={
                    "model": AI_CONFIG["ollama_model"],
                    "prompt": prompt,
                    "stream": False,
                },
                timeout=30,
            )
            if response.ok:
                return response.json().get("response")
        except Exception as e:
            print(f"⚠️ Ollama not available: {e}")
        return None
    
    def _ask_claude(self, prompt: str) -> Optional[str]:
        """Ask Claude API for help (paid fallback)."""
        # TODO: Implement Claude API call
        print("⚠️ Claude API not implemented yet")
        return None
