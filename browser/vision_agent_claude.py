"""
Vision AI Agent using Claude API.
Fills forms by analyzing screenshots with Claude Vision.
"""

import base64
import json
import time
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

import anthropic


class ClaudeVisionAgent:
    """AI agent that fills forms using Claude Vision API."""
    
    def __init__(self, page, profile: dict):
        self.page = page
        self.profile = profile
        self.client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        self.fields_filled = []
        
        # Build profile summary
        p = profile.get('personal', {})
        w = profile.get('work_experience', [{}])[0] if profile.get('work_experience') else {}
        e = profile.get('education', [{}])[0] if profile.get('education') else {}
        links = profile.get('links', {})
        
        self.profile_summary = f"""
First Name: {p.get('first_name', '')}
Last Name: {p.get('last_name', '')}
Email: {p.get('email', '')}
Phone: {p.get('phone', '')}
Location: {p.get('location', 'Raleigh, NC')}
Country: United States
LinkedIn: {links.get('linkedin', '')}
Current Employer: {w.get('company', '')}
Current Title: {w.get('title', '')}
School: {e.get('school', '')}
Degree: {e.get('degree', '')}
US Work Authorization: Yes
Needs Sponsorship: No
Remote Work: Yes
Previously at company: No
"""
    
    def take_screenshot(self) -> bytes:
        """Take screenshot of current viewport."""
        return self.page.screenshot(type="png")
    
    def analyze_and_get_action(self, screenshot: bytes) -> dict:
        """Use Claude Vision to analyze screenshot and decide next action."""
        
        b64_image = base64.standard_b64encode(screenshot).decode("utf-8")
        
        prompt = f"""You are filling out a job application form. Analyze this screenshot.

PROFILE DATA:
{self.profile_summary}

TASK: Look at the form and tell me what to do next.

If you see an empty text field that needs to be filled:
Return: {{"action": "fill", "field_label": "label text", "value": "value to enter"}}

If you see a dropdown/select that needs selection:
Return: {{"action": "select", "field_label": "label text", "value": "option to select"}}

If you see a checkbox that should be checked:
Return: {{"action": "check", "field_label": "label text"}}

If you see a file upload for resume:
Return: {{"action": "upload_resume", "field_label": "resume"}}

If you need to scroll down to see more fields:
Return: {{"action": "scroll_down"}}

If the form appears complete or you see a Submit button:
Return: {{"action": "done"}}

Return ONLY valid JSON, no other text."""

        try:
            message = self.client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=500,
                messages=[{
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": b64_image,
                            },
                        },
                        {"type": "text", "text": prompt}
                    ],
                }],
            )
            
            response_text = message.content[0].text.strip()
            
            # Extract JSON from response
            if "{" in response_text:
                json_start = response_text.find("{")
                json_end = response_text.rfind("}") + 1
                json_str = response_text[json_start:json_end]
                return json.loads(json_str)
            
        except Exception as e:
            print(f"Claude Vision error: {e}")
        
        return {"action": "error"}
    
    def execute_action(self, action: dict) -> bool:
        """Execute the action returned by Claude."""
        action_type = action.get("action", "")
        field_label = action.get("field_label", "")
        value = action.get("value", "")
        
        print(f"  🎯 Action: {action_type} | {field_label} | {value[:30] if value else ''}")
        
        try:
            if action_type == "fill":
                # Find and fill the field
                el = self._find_field(field_label)
                if el:
                    el.fill(value)
                    self.fields_filled.append(field_label)
                    print(f"  ✅ Filled: {field_label}")
                    return True
                    
            elif action_type == "select":
                el = self._find_field(field_label)
                if el:
                    el.click()
                    time.sleep(0.3)
                    # Try to find and click option
                    option = self.page.query_selector(f"li:has-text('{value}')")
                    if not option:
                        option = self.page.query_selector(f"option:has-text('{value}')")
                    if option:
                        option.click()
                        self.fields_filled.append(field_label)
                        print(f"  ✅ Selected: {value}")
                        return True
                        
            elif action_type == "check":
                el = self._find_field(field_label, field_type="checkbox")
                if el:
                    el.check()
                    self.fields_filled.append(field_label)
                    print(f"  ✅ Checked: {field_label}")
                    return True
                    
            elif action_type == "scroll_down":
                self.page.evaluate("window.scrollBy(0, 500)")
                time.sleep(0.3)
                print(f"  ⬇️ Scrolled down")
                return True
                
            elif action_type == "upload_resume":
                # Find file input and upload
                resume_path = self.profile.get('files', {}).get('resume_path', '')
                if resume_path and Path(resume_path).exists():
                    file_input = self.page.query_selector("input[type='file']")
                    if file_input:
                        file_input.set_input_files(resume_path)
                        self.fields_filled.append("resume")
                        print(f"  ✅ Uploaded resume")
                        return True
                        
            elif action_type == "done":
                print(f"  🏁 Form complete!")
                return False
                
            elif action_type == "error":
                print(f"  ❌ Error analyzing screenshot")
                return False
                
        except Exception as e:
            print(f"  ❌ Action failed: {e}")
        
        return True  # Continue even if action failed
    
    def _find_field(self, label: str, field_type: str = None) -> object:
        """Find form field by label."""
        label_lower = label.lower()
        
        # Try different selectors
        selectors = []
        
        if field_type == "checkbox":
            selectors = [
                f"input[type='checkbox'][id*='{label_lower}']",
                f"label:has-text('{label}') input[type='checkbox']",
            ]
        else:
            selectors = [
                f"input[aria-label*='{label}']",
                f"input[placeholder*='{label}']",
                f"input[id*='{label_lower.replace(' ', '_')}']",
                f"input[id*='{label_lower.replace(' ', '-')}']",
                f"label:has-text('{label}') + input",
                f"label:has-text('{label}') input",
            ]
        
        # Try main page
        for sel in selectors:
            try:
                el = self.page.query_selector(sel)
                if el and el.is_visible():
                    return el
            except:
                pass
        
        # Try frames
        for frame in self.page.frames:
            for sel in selectors:
                try:
                    el = frame.query_selector(sel)
                    if el and el.is_visible():
                        return el
                except:
                    pass
        
        return None
    
    def run(self, max_steps: int = 20) -> dict:
        """Run the agent to fill the form."""
        print(f"\n🤖 Claude Vision Agent starting...")
        print(f"   Profile: {self.profile.get('personal', {}).get('first_name', 'Unknown')}")
        
        for step in range(max_steps):
            print(f"\n--- Step {step + 1} ---")
            
            # Take screenshot
            screenshot = self.take_screenshot()
            
            # Analyze and get action
            action = self.analyze_and_get_action(screenshot)
            
            # Execute action
            should_continue = self.execute_action(action)
            
            if not should_continue:
                break
            
            time.sleep(0.5)
        
        result = {
            "success": len(self.fields_filled) > 0,
            "fields_filled": self.fields_filled,
            "steps": step + 1,
        }
        
        print(f"\n🤖 Done: {len(self.fields_filled)} fields filled in {step + 1} steps")
        return result
