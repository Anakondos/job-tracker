"""
V7 Form Filler Agent
Uses Claude Vision to understand and fill forms like a human.
"""

import anthropic
import base64
import json
import time
from dataclasses import dataclass
from typing import List, Dict, Optional
from pathlib import Path
from playwright.sync_api import sync_playwright, Page


@dataclass
class Profile:
    """User profile for form filling"""
    first_name: str = "Anton"
    last_name: str = "Kondakov"
    email: str = "anton.kondakov.PM@gmail.com"
    phone: str = "9105360602"
    location: str = "Wake Forest, NC"
    address: str = "2000 Sweet Samson Street, Wake Forest, NC 27587"
    linkedin: str = "https://linkedin.com/in/antonkondakov"
    current_company: str = "DXC Technology"
    
    # Work authorization
    work_authorized: str = "Yes"
    sponsorship_required: str = "No"
    
    # EEO
    gender: str = "Male"
    hispanic: str = "No"
    race: str = "White"
    veteran: str = "I am not a protected veteran"
    disability: str = "No, I do not have a disability"
    
    def to_context(self) -> str:
        """Return profile as context string for Claude"""
        return f"""
Applicant Profile:
- Name: {self.first_name} {self.last_name}
- Email: {self.email}
- Phone: {self.phone}
- Location: {self.location}
- Full Address: {self.address}
- LinkedIn: {self.linkedin}
- Current Company: {self.current_company}
- Work Authorization: Authorized to work in US, no sponsorship needed
- EEO: {self.gender}, {self.race}, Not Hispanic, Not a veteran, No disability
"""


def get_api_key() -> str:
    """Get Anthropic API key"""
    import os
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        return key
    
    config_path = Path(__file__).parent.parent / "v5" / "config" / "api_keys.json"
    if config_path.exists():
        with open(config_path) as f:
            return json.load(f).get("anthropic_api_key")
    
    raise ValueError("No API key found")


class FormFillerAgent:
    """
    AI Agent that fills forms by looking at screenshots.
    Works like a human - sees the form, decides what to fill, fills it.
    """
    
    SYSTEM_PROMPT = """You are a form-filling assistant. You look at job application forms and fill them out.

Your task:
1. Analyze the screenshot of a form
2. Identify visible input fields that need to be filled
3. Return a list of actions to fill them

IMPORTANT RULES:
- Only fill fields that are VISIBLE in the screenshot
- Return actions in order from TOP to BOTTOM
- For dropdowns, first click to open, then select value
- After filling visible fields, scroll down if there might be more
- When form is complete, indicate ready to submit

Return JSON array of actions:
[
  {"action": "fill", "field": "First Name", "value": "Anton", "selector_hint": "input near 'First Name' label"},
  {"action": "click", "field": "Country dropdown", "selector_hint": "dropdown with country selection"},
  {"action": "select", "field": "Country", "value": "United States"},
  {"action": "scroll", "direction": "down"},
  {"action": "upload", "field": "Resume", "file_type": "resume"},
  {"action": "complete", "message": "Form is filled, ready for review"}
]

Action types:
- fill: Type text into input field
- click: Click on element (button, dropdown, checkbox)
- select: Select option from opened dropdown
- scroll: Scroll the page
- upload: Upload a file (resume or cover letter)
- complete: Form is done

Be precise with field identification. Use visible labels and placeholders."""

    def __init__(self):
        self.client = anthropic.Anthropic(api_key=get_api_key())
        self.profile = Profile()
        self.page: Optional[Page] = None
        self.playwright = None
        self.browser = None
        self.max_iterations = 20
        self.actions_log = []
        
    def connect(self, port: int = 9222):
        """Connect to Chrome via CDP"""
        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.connect_over_cdp(f"http://localhost:{port}")
        context = self.browser.contexts[0]
        
        if context.pages:
            self.page = context.pages[0]
        else:
            self.page = context.new_page()
            
        print(f"✅ Connected to Chrome")
        print(f"   URL: {self.page.url[:60]}")
        
    def screenshot_base64(self) -> str:
        """Take screenshot and return as base64"""
        img_bytes = self.page.screenshot(type="jpeg", quality=80)
        return base64.standard_b64encode(img_bytes).decode("utf-8")
    
    def analyze_form(self, screenshot_b64: str) -> List[Dict]:
        """Send screenshot to Claude and get list of actions"""
        
        response = self.client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2000,
            system=self.SYSTEM_PROMPT,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": screenshot_b64
                        }
                    },
                    {
                        "type": "text",
                        "text": f"""Analyze this job application form screenshot.

{self.profile.to_context()}

What fields do you see that need to be filled? Return JSON array of actions to fill them.
Only include fields that are VISIBLE and EMPTY in the screenshot.
If you see fields already filled correctly, skip them.
If you need to scroll to see more fields, include scroll action at the end."""
                    }
                ]
            }]
        )
        
        # Parse JSON from response
        text = response.content[0].text
        
        # Extract JSON from response (might be wrapped in markdown)
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0]
        elif "```" in text:
            text = text.split("```")[1].split("```")[0]
            
        try:
            actions = json.loads(text.strip())
            return actions
        except json.JSONDecodeError:
            print(f"⚠️ Failed to parse actions: {text[:200]}")
            return []
    
    def execute_action(self, action: Dict) -> bool:
        """Execute a single action on the page"""
        action_type = action.get("action")
        field = action.get("field", "")
        value = action.get("value", "")
        
        print(f"   → {action_type}: {field}", end="")
        
        try:
            if action_type == "fill":
                # Find field by label text
                element = self.find_field(field, action.get("selector_hint", ""))
                if element:
                    element.scroll_into_view_if_needed()
                    element.fill(value)
                    print(f" = '{value[:20]}' ✓")
                    return True
                else:
                    print(f" - NOT FOUND ✗")
                    return False
                    
            elif action_type == "click":
                element = self.find_clickable(field, action.get("selector_hint", ""))
                if element:
                    element.scroll_into_view_if_needed()
                    element.click()
                    time.sleep(0.3)
                    print(f" ✓")
                    return True
                else:
                    print(f" - NOT FOUND ✗")
                    return False
                    
            elif action_type == "select":
                # Click on option in dropdown
                option = self.page.get_by_text(value, exact=False).first
                if option:
                    option.click()
                    time.sleep(0.2)
                    print(f" = '{value}' ✓")
                    return True
                else:
                    # Try keyboard
                    self.page.keyboard.type(value[:10])
                    time.sleep(0.3)
                    self.page.keyboard.press("Enter")
                    print(f" = '{value}' (keyboard) ✓")
                    return True
                    
            elif action_type == "scroll":
                direction = action.get("direction", "down")
                if direction == "down":
                    self.page.keyboard.press("PageDown")
                else:
                    self.page.keyboard.press("PageUp")
                time.sleep(0.5)
                print(f" {direction} ✓")
                return True
                
            elif action_type == "upload":
                file_type = action.get("file_type", "resume")
                # Find file input
                file_input = self.page.query_selector('input[type="file"]')
                if file_input:
                    # TODO: Get actual file path from profile
                    print(f" - TODO: implement file upload ✓")
                    return True
                else:
                    print(f" - no file input found ✗")
                    return False
                    
            elif action_type == "complete":
                print(f" - {action.get('message', 'Done')} ✓")
                return True
                
            else:
                print(f" - unknown action ✗")
                return False
                
        except Exception as e:
            print(f" - ERROR: {str(e)[:40]} ✗")
            return False
    
    def find_field(self, label: str, hint: str = "") -> Optional[object]:
        """Find input field by label text"""
        # Try various strategies
        
        # 1. By label
        try:
            label_el = self.page.get_by_label(label, exact=False).first
            if label_el:
                return label_el
        except:
            pass
        
        # 2. By placeholder
        try:
            placeholder_el = self.page.get_by_placeholder(label, exact=False).first
            if placeholder_el:
                return placeholder_el
        except:
            pass
        
        # 3. By text near input
        try:
            # Find text, then find nearby input
            text_el = self.page.get_by_text(label, exact=False).first
            if text_el:
                # Get parent and find input
                input_el = text_el.locator(".. >> input, .. >> textarea").first
                if input_el:
                    return input_el
        except:
            pass
        
        # 4. By common selectors based on label
        label_lower = label.lower()
        selectors = []
        
        if "first" in label_lower and "name" in label_lower:
            selectors = ["#first_name", "#firstName", "input[name*='first']"]
        elif "last" in label_lower and "name" in label_lower:
            selectors = ["#last_name", "#lastName", "input[name*='last']"]
        elif "email" in label_lower:
            selectors = ["#email", "input[type='email']", "input[name*='email']"]
        elif "phone" in label_lower:
            selectors = ["#phone", "input[type='tel']", "input[name*='phone']"]
        elif "linkedin" in label_lower:
            selectors = ["input[name*='linkedin']", "input[placeholder*='linkedin']"]
            
        for sel in selectors:
            try:
                el = self.page.query_selector(sel)
                if el:
                    return el
            except:
                pass
        
        return None
    
    def find_clickable(self, label: str, hint: str = "") -> Optional[object]:
        """Find clickable element by label"""
        try:
            # Try button
            btn = self.page.get_by_role("button", name=label).first
            if btn:
                return btn
        except:
            pass
        
        try:
            # Try by text
            el = self.page.get_by_text(label, exact=False).first
            if el:
                return el
        except:
            pass
        
        try:
            # Try combobox/dropdown
            combo = self.page.get_by_role("combobox").filter(has_text=label).first
            if combo:
                return combo
        except:
            pass
        
        return None
    
    def fill_form(self, url: str = None):
        """Main loop: analyze screenshot, execute actions, repeat"""
        
        if url:
            print(f"📍 Opening: {url[:60]}...")
            self.page.goto(url, wait_until="domcontentloaded", timeout=30000)
            time.sleep(2)
        
        print("\n" + "=" * 60)
        print("V7 AGENT FORM FILLER")
        print("=" * 60)
        
        for iteration in range(self.max_iterations):
            print(f"\n[Iteration {iteration + 1}]")
            
            # 1. Take screenshot
            print("📸 Taking screenshot...")
            screenshot = self.screenshot_base64()
            
            # 2. Analyze with Claude
            print("🤖 Analyzing form...")
            actions = self.analyze_form(screenshot)
            
            if not actions:
                print("   No actions returned")
                continue
            
            # 3. Check if complete
            if any(a.get("action") == "complete" for a in actions):
                print("\n✅ Form filling complete!")
                break
            
            # 4. Execute actions
            print(f"📝 Executing {len(actions)} actions:")
            for action in actions:
                success = self.execute_action(action)
                self.actions_log.append({**action, "success": success})
                time.sleep(0.2)
            
            # Small pause between iterations
            time.sleep(0.5)
        
        print("\n" + "=" * 60)
        print("Browser stays open. Review and submit manually.")
        print("=" * 60)
    
    def close(self):
        """Clean up"""
        if self.playwright:
            self.playwright.stop()


# CLI entry point
if __name__ == "__main__":
    import sys
    
    url = sys.argv[1] if len(sys.argv) > 1 else None
    
    agent = FormFillerAgent()
    
    try:
        agent.connect()
        agent.fill_form(url)
    except KeyboardInterrupt:
        print("\n⚠️ Interrupted")
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        input("\nPress Enter to close...")
        agent.close()
