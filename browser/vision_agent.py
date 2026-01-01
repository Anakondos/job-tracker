"""
Vision AI Agent v3 - fills forms using Tab navigation.
Improved field type matching.
"""

import base64
import json
import time
import re
import requests
from pathlib import Path


OLLAMA_URL = "http://localhost:11434/api/generate"
VISION_MODEL = "llava:7b"


def encode_image(image_bytes: bytes) -> str:
    return base64.b64encode(image_bytes).decode("utf-8")


def ask_vision(image_bytes: bytes, prompt: str) -> str:
    try:
        payload = {
            "model": VISION_MODEL,
            "prompt": prompt,
            "images": [encode_image(image_bytes)],
            "stream": False,
            "options": {"temperature": 0.1}
        }
        
        resp = requests.post(OLLAMA_URL, json=payload, timeout=120)
        if resp.status_code == 200:
            return resp.json().get("response", "").strip()
    except Exception as e:
        print(f"Vision error: {e}")
    return ""


class VisionFormAgent:
    """AI agent that fills forms using Tab navigation."""
    
    def __init__(self, page, profile: dict):
        self.page = page
        self.profile = profile
        self.actions_log = []
        self.max_iterations = 30
        self.fields_filled = set()
        
        # Pre-compute answers
        p = profile.get('personal', {})
        links = profile.get('links', {})
        
        self.field_answers = {
            'first name': p.get('first_name', ''),
            'last name': p.get('last_name', ''),
            'full name': f"{p.get('first_name', '')} {p.get('last_name', '')}".strip(),
            'name': f"{p.get('first_name', '')} {p.get('last_name', '')}".strip(),
            'your name': f"{p.get('first_name', '')} {p.get('last_name', '')}".strip(),
            'email': p.get('email', ''),
            'e-mail': p.get('email', ''),
            'phone': p.get('phone', ''),
            'telephone': p.get('phone', ''),
            'mobile': p.get('phone', ''),
            'linkedin': links.get('linkedin', ''),
            'github': links.get('github', ''),
            'portfolio': links.get('portfolio', ''),
            'website': links.get('portfolio', ''),
            'city': p.get('city', 'Raleigh'),
            'state': p.get('state', 'NC'),
            'location': f"{p.get('city', 'Raleigh')}, {p.get('state', 'NC')}",
            'country': 'United States',
            'authorized': 'Yes',
            'work authorization': 'Yes',
            'legally authorized': 'Yes',
            'sponsorship': 'No',
            'require sponsorship': 'No',
            'visa': 'No',
            'years': '15',
            'experience': '15',
            'years of experience': '15',
            'how did you hear': 'Company website',
            'source': 'Company website',
        }
    
    def take_screenshot(self) -> bytes:
        return self.page.screenshot(type="png")
    
    def find_answer(self, field_desc: str) -> str:
        field_lower = field_desc.lower()
        
        for key, value in self.field_answers.items():
            if key in field_lower:
                return value
        
        questions = self.profile.get('questions', {})
        for q_key, q_val in questions.items():
            if q_key.lower() in field_lower:
                return str(q_val)
        
        return ""
    
    def is_text_field(self, field_type: str) -> bool:
        """Check if field type indicates a text input."""
        ft = field_type.lower()
        return any(x in ft for x in ['text', 'input', 'email', 'tel', 'textarea'])
    
    def is_dropdown(self, field_type: str) -> bool:
        """Check if field type indicates a dropdown/select."""
        ft = field_type.lower()
        return any(x in ft for x in ['dropdown', 'select', 'combo', 'list'])
    
    def is_checkbox(self, field_type: str) -> bool:
        """Check if field type indicates a checkbox."""
        ft = field_type.lower()
        return any(x in ft for x in ['checkbox', 'check', 'toggle', 'radio'])
    
    def is_button(self, field_type: str) -> bool:
        """Check if field type indicates a button."""
        ft = field_type.lower()
        return any(x in ft for x in ['button', 'submit', 'apply', 'send'])
    
    def analyze_screen(self, screenshot: bytes) -> dict:
        """Ask AI what field is currently focused."""
        
        prompt = """Look at this job application form screenshot.

Find the currently focused/active input field - look for:
- A field with a blinking cursor
- A highlighted or outlined input box
- The first visible empty field

Tell me about THIS SPECIFIC FIELD:
1. What type is it? (text, dropdown, checkbox, button)
2. What is the label/question for this field?
3. Is it empty?

Respond with ONLY this JSON format:
{"field_type": "text", "label": "First Name", "is_empty": true}

Or if you see a submit button:
{"field_type": "button", "label": "Submit Application"}

Or if more scrolling needed:
{"needs_scroll": true}

IMPORTANT: Respond with ONLY the JSON."""

        response = ask_vision(screenshot, prompt)
        
        try:
            match = re.search(r'\{[^}]+\}', response)
            if match:
                return json.loads(match.group())
        except json.JSONDecodeError as e:
            print(f"  Parse error: {response[:100]}")
        
        return {"field_type": "unknown", "label": ""}
    
    def execute_action(self, field_info: dict) -> str:
        """Execute action based on field analysis."""
        
        field_type = field_info.get("field_type", "unknown")
        label = field_info.get("label", "")
        is_empty = field_info.get("is_empty", True)
        
        if field_info.get("needs_scroll"):
            print("  ↓ Scrolling down...")
            self.page.mouse.wheel(0, 300)
            time.sleep(0.3)
            return "scroll"
        
        # Detect submit button
        if self.is_button(field_type) or 'submit' in label.lower() or 'apply' in label.lower():
            if 'submit' in label.lower() or 'apply' in field_type.lower():
                print(f"  🎯 Found Submit button: '{label}'")
                return "ready_to_submit"
        
        # Handle text fields
        if self.is_text_field(field_type):
            if is_empty and label:
                answer = self.find_answer(label)
                if answer:
                    print(f"  ✏️  Typing '{answer}' into '{label}'")
                    self.page.keyboard.type(answer, delay=15)
                    self.fields_filled.add(label)
                    time.sleep(0.1)
                    self.page.keyboard.press("Tab")
                    return f"typed:{answer[:20]}"
                else:
                    print(f"  ⏭️  No answer for '{label}'")
            else:
                print(f"  ⏭️  Field '{label}' - skip (empty={is_empty})")
            
            self.page.keyboard.press("Tab")
            return "skip"
        
        # Handle dropdowns
        if self.is_dropdown(field_type):
            answer = self.find_answer(label)
            if answer:
                print(f"  📋 Selecting '{answer}' for '{label}'")
                self.page.keyboard.type(answer, delay=15)
                time.sleep(0.2)
                self.page.keyboard.press("Enter")
                self.fields_filled.add(label)
            self.page.keyboard.press("Tab")
            return "selected" if answer else "skip"
        
        # Handle checkboxes
        if self.is_checkbox(field_type):
            answer = self.find_answer(label)
            if answer and answer.lower() in ('yes', 'true', '1'):
                print(f"  ☑️  Checking '{label}'")
                self.page.keyboard.press("Space")
            self.page.keyboard.press("Tab")
            return "checked" if answer else "skip"
        
        # Unknown - just tab
        print(f"  ⏭️  Unknown: type='{field_type}', label='{label}'")
        self.page.keyboard.press("Tab")
        return "tab"
    
    def fill_form(self) -> dict:
        """Main loop: analyze → act → repeat."""
        
        print("\n🤖 Vision Agent v3 starting...")
        print(f"   Profile: {self.profile.get('personal', {}).get('first_name', 'Unknown')}")
        
        result = {
            "success": False,
            "actions_taken": 0,
            "iterations": 0,
            "fields_filled": [],
            "status": "running"
        }
        
        # Initial focus
        print("\n  📍 Focusing form...")
        self.page.keyboard.press("Tab")
        time.sleep(0.3)
        
        consecutive_skips = 0
        
        for i in range(self.max_iterations):
            result["iterations"] = i + 1
            print(f"\n--- Step {i + 1} ---")
            
            screenshot = self.take_screenshot()
            
            # Save debug screenshot
            with open(f"/tmp/vision_step_{i+1}.png", "wb") as f:
                f.write(screenshot)
            
            print("  🔍 Analyzing...")
            field_info = self.analyze_screen(screenshot)
            print(f"  📋 Field: {field_info}")
            
            action = self.execute_action(field_info)
            result["actions_taken"] += 1
            
            # Track progress
            if action.startswith("typed:") or action == "selected" or action == "checked":
                consecutive_skips = 0
            elif action in ("skip", "tab"):
                consecutive_skips += 1
            elif action == "ready_to_submit":
                print("\n✅ Form ready to submit!")
                result["success"] = True
                result["status"] = "ready_to_submit"
                break
            
            if consecutive_skips > 8:
                print("\n⚠️  Many skips - may be at form end")
                result["status"] = "may_be_complete"
                break
            
            time.sleep(0.3)
        
        result["fields_filled"] = list(self.fields_filled)
        print(f"\n🤖 Done: {len(result['fields_filled'])} fields filled in {result['iterations']} steps")
        
        return result


if __name__ == "__main__":
    print("Vision Agent v3 - run via test script")
