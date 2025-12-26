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
    
    def __init__(self, headless: bool = False):
        """
        Initialize browser client.
        
        Args:
            headless: Run in headless mode (default False for Cloudflare bypass)
        """
        self.headless = headless
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
            "#candidate-location": profile.get("personal.location"),
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
