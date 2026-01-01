"""
AI Agent for browser automation.
Uses Ollama to analyze page and decide what actions to take.
"""

import json
import time
from typing import Optional, Dict, List
import requests


OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "llama3.2:3b"


def ask_ollama(prompt: str, system: str = None) -> Optional[str]:
    """Make a request to Ollama API."""
    try:
        payload = {
            "model": MODEL,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.1}
        }
        if system:
            payload["system"] = system
        
        resp = requests.post(OLLAMA_URL, json=payload, timeout=120)
        if resp.status_code == 200:
            return resp.json().get("response", "").strip()
    except Exception as e:
        print(f"Ollama error: {e}")
    return None


class AIFormAgent:
    """AI agent that can analyze and fill forms."""
    
    def __init__(self, page, profile_data: dict):
        self.page = page
        self.profile = profile_data
        self.actions_taken = []
    
    def get_page_context(self, max_length: int = 4000) -> str:
        """Get relevant page content for AI analysis."""
        # Get visible form fields
        html = self.page.content()
        
        # Extract just form-related elements
        forms_text = []
        
        # Get all labels and inputs
        labels = self.page.query_selector_all("label")
        for label in labels[:30]:  # Limit to 30 fields
            try:
                text = label.inner_text().strip()[:100]
                if text:
                    forms_text.append(f"LABEL: {text}")
            except:
                pass
        
        inputs = self.page.query_selector_all("input, select, textarea")
        for inp in inputs[:30]:
            try:
                name = inp.get_attribute("name") or inp.get_attribute("id") or ""
                placeholder = inp.get_attribute("placeholder") or ""
                value = inp.get_attribute("value") or ""
                inp_type = inp.get_attribute("type") or "text"
                forms_text.append(f"INPUT: name={name}, type={inp_type}, placeholder={placeholder}, value={value}")
            except:
                pass
        
        return "\n".join(forms_text)[:max_length]
    
    def analyze_and_act(self) -> Dict:
        """Have AI analyze page and decide what to do."""
        
        system = """You are a form-filling assistant. Analyze the form fields and user profile, then decide what actions to take.

For each unfilled field, provide an action in JSON format:
{
    "actions": [
        {"type": "fill", "selector": "CSS selector", "value": "value to fill"},
        {"type": "select", "selector": "CSS selector", "value": "option to select"},
        {"type": "click", "selector": "CSS selector"},
        {"type": "skip", "reason": "why skip this field"}
    ],
    "analysis": "brief explanation",
    "questions_found": ["any questions that need human input"]
}

Rules:
- Use exact CSS selectors like #field_id, input[name='field'], label:has-text('Field')
- Match profile data to form fields intelligently
- For dropdowns/selects, use the visible option text
- If a question requires specific answer not in profile, add to questions_found
"""

        page_context = self.get_page_context()
        
        prompt = f"""Analyze this form and decide what to fill:

FORM FIELDS:
{page_context}

USER PROFILE:
{json.dumps(self.profile, indent=2)[:1500]}

What actions should I take? Respond with JSON only."""

        response = ask_ollama(prompt, system)
        
        if response:
            try:
                # Extract JSON from response
                start = response.find('{')
                end = response.rfind('}') + 1
                if start >= 0 and end > start:
                    return json.loads(response[start:end])
            except json.JSONDecodeError:
                pass
        
        return {"actions": [], "analysis": "Could not analyze", "questions_found": []}
    
    def execute_actions(self, actions: List[Dict]) -> int:
        """Execute the actions suggested by AI."""
        executed = 0
        
        for action in actions:
            try:
                action_type = action.get("type")
                selector = action.get("selector", "")
                value = action.get("value", "")
                
                if action_type == "skip":
                    print(f"  ⏭️ Skipping: {action.get('reason', 'unknown')}")
                    continue
                
                element = self.page.query_selector(selector)
                if not element or not element.is_visible():
                    print(f"  ⚠️ Element not found: {selector}")
                    continue
                
                if action_type == "fill":
                    element.scroll_into_view_if_needed()
                    element.fill(value)
                    print(f"  ✅ Filled: {selector} = {value[:30]}...")
                    executed += 1
                    
                elif action_type == "select":
                    element.scroll_into_view_if_needed()
                    element.click()
                    time.sleep(0.3)
                    self.page.keyboard.type(value, delay=30)
                    time.sleep(0.3)
                    self.page.keyboard.press("Enter")
                    print(f"  ✅ Selected: {selector} = {value}")
                    executed += 1
                    
                elif action_type == "click":
                    element.scroll_into_view_if_needed()
                    element.click()
                    print(f"  ✅ Clicked: {selector}")
                    executed += 1
                    
                time.sleep(0.2)
                
            except Exception as e:
                print(f"  ❌ Action failed: {action} - {e}")
        
        return executed
    
    def fill_form_with_ai(self, max_iterations: int = 3) -> Dict:
        """Main loop: analyze page, take actions, repeat."""
        
        result = {
            "total_actions": 0,
            "iterations": 0,
            "questions": [],
            "success": False
        }
        
        print("\n🤖 AI Agent starting form analysis...")
        
        for i in range(max_iterations):
            result["iterations"] = i + 1
            print(f"\n--- Iteration {i + 1} ---")
            
            # Scroll to see form
            self.page.evaluate("window.scrollTo(0, 0)")
            time.sleep(0.5)
            
            # AI analyzes and decides
            analysis = self.analyze_and_act()
            
            print(f"Analysis: {analysis.get('analysis', 'N/A')}")
            
            actions = analysis.get("actions", [])
            questions = analysis.get("questions_found", [])
            
            if questions:
                result["questions"].extend(questions)
                print(f"❓ Questions found: {questions}")
            
            if not actions:
                print("No more actions needed")
                result["success"] = True
                break
            
            # Execute actions
            executed = self.execute_actions(actions)
            result["total_actions"] += executed
            
            if executed == 0:
                print("No actions executed, stopping")
                break
            
            # Scroll down to see more fields
            self.page.evaluate("window.scrollBy(0, 400)")
            time.sleep(0.5)
        
        print(f"\n🤖 AI Agent finished: {result['total_actions']} actions in {result['iterations']} iterations")
        return result


def answer_custom_question(question: str, profile: dict, answer_library: dict = None) -> Optional[str]:
    """Use AI to answer a custom application question."""
    
    system = """You are helping fill out a job application. Answer the question based on the profile data.
    
Rules:
- Be concise and professional
- If it's a yes/no or number question, give just the answer
- If asking about experience years, calculate from profile
- If you don't have enough info, say "NEED_INPUT"

Respond with just the answer, nothing else."""

    library_context = ""
    if answer_library:
        library_context = f"\nPREVIOUS ANSWERS:\n{json.dumps(answer_library, indent=2)[:1000]}"
    
    prompt = f"""Answer this application question:

QUESTION: {question}

PROFILE:
{json.dumps(profile, indent=2)[:1000]}
{library_context}

Answer:"""

    response = ask_ollama(prompt, system)
    return response if response and response != "NEED_INPUT" else None
