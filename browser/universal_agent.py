"""
Universal Job Agent - AI-powered agent that can navigate any job site.

Architecture:
- Browser: Playwright for web automation
- Vision: LLaVA (local) / Claude (cloud) for screen understanding  
- Knowledge: Profile + learned patterns + answer cache
- Loop: Screenshot → Analyze → Act → Repeat
"""

import json
import time
import base64
import requests
from pathlib import Path
from typing import Optional, Dict, List, Any
from dataclasses import dataclass, field
from enum import Enum


class PageType(Enum):
    UNKNOWN = "unknown"
    CAREERS_HOME = "careers_home"       # Main careers page
    JOB_LISTING = "job_listing"         # List of jobs
    JOB_DETAIL = "job_detail"           # Single job description
    APPLICATION_FORM = "application"    # Application form
    LOGIN_REQUIRED = "login_required"   # Need to login
    SUCCESS = "success"                 # Application submitted
    ERROR = "error"                     # Error page


class ActionType(Enum):
    CLICK = "click"
    TYPE = "type"
    SELECT = "select"
    SCROLL = "scroll"
    UPLOAD = "upload"
    WAIT = "wait"
    ASK_USER = "ask_user"
    DONE = "done"
    ERROR = "error"


@dataclass
class PageAnalysis:
    """Result of analyzing a screenshot."""
    page_type: PageType
    elements: List[Dict[str, Any]]
    suggested_action: Optional[Dict[str, Any]]
    raw_response: str = ""
    confidence: float = 0.0


@dataclass
class AgentAction:
    """Action for the agent to perform."""
    action_type: ActionType
    target: str = ""            # Selector or description
    value: str = ""             # Value to type/select
    reason: str = ""            # Why this action
    confidence: float = 0.0


@dataclass
class AgentState:
    """Current state of the agent."""
    url: str
    goal: str
    step: int = 0
    history: List[Dict] = field(default_factory=list)
    extracted_data: Dict = field(default_factory=dict)
    status: str = "running"


class VisionProvider:
    """Abstract vision provider - local or cloud."""
    
    def analyze(self, screenshot: bytes, prompt: str) -> str:
        raise NotImplementedError


class LLaVAProvider(VisionProvider):
    """Local LLaVA via Ollama."""
    
    def __init__(self, model: str = "llava:7b", url: str = "http://localhost:11434"):
        self.model = model
        self.url = url
    
    def analyze(self, screenshot: bytes, prompt: str) -> str:
        image_b64 = base64.b64encode(screenshot).decode("utf-8")
        
        payload = {
            "model": self.model,
            "prompt": prompt,
            "images": [image_b64],
            "stream": False,
            "options": {"temperature": 0.1}
        }
        
        try:
            resp = requests.post(f"{self.url}/api/generate", json=payload, timeout=120)
            if resp.ok:
                return resp.json().get("response", "").strip()
        except Exception as e:
            print(f"LLaVA error: {e}")
        
        return ""


class ClaudeVisionProvider(VisionProvider):
    """Claude Vision API (requires API key)."""
    
    def __init__(self, api_key: str = None):
        self.api_key = api_key or ""
    
    def analyze(self, screenshot: bytes, prompt: str) -> str:
        # TODO: Implement Claude Vision API
        # For now, fallback to description
        return "Claude Vision not implemented yet"


class KnowledgeBase:
    """
    Stores profile, learned patterns, and cached answers.
    """
    
    def __init__(self, profile_path: Path = None, db_path: Path = None):
        self.profile = self._load_profile(profile_path)
        self.patterns = {}      # Learned field patterns
        self.answers = {}       # Cached answers by question hash
        self.db_path = db_path
        
        if db_path and db_path.exists():
            self._load_db()
    
    def _load_profile(self, path: Path = None) -> dict:
        if path and path.exists():
            with open(path) as f:
                return json.load(f)
        return {}
    
    def _load_db(self):
        with open(self.db_path) as f:
            data = json.load(f)
            self.patterns = data.get("patterns", {})
            self.answers = data.get("answers", {})
    
    def save(self):
        if self.db_path:
            with open(self.db_path, "w") as f:
                json.dump({
                    "patterns": self.patterns,
                    "answers": self.answers
                }, f, indent=2)
    
    def get_profile_value(self, key: str) -> str:
        """Get value from profile by dot-notation key."""
        parts = key.split(".")
        value = self.profile
        
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
    
    def find_answer(self, question: str) -> Optional[str]:
        """Find answer for a question from patterns or cache."""
        q_lower = question.lower()
        
        # Check known patterns
        pattern_answers = {
            "first name": self.get_profile_value("personal.first_name"),
            "last name": self.get_profile_value("personal.last_name"),
            "email": self.get_profile_value("personal.email"),
            "phone": self.get_profile_value("personal.phone"),
            "linkedin": self.get_profile_value("links.linkedin"),
            "location": self.get_profile_value("personal.location"),
            "city": self.get_profile_value("personal.city"),
            "country": self.get_profile_value("personal.country"),
            "18 years": "Yes",
            "authorized to work": "Yes",
            "legally authorized": "Yes",
            "sponsorship": "No",
            "require sponsorship": "No",
            "visa": "No",
            "previously employed": "No",
            "referred": "No",
            "how did you hear": "Company website",
            "gender": "Decline to self-identify",
            "veteran": "I am not a protected veteran",
            "disability": "I do not want to answer",
            "race": "Decline to self-identify",
            "ethnicity": "Decline to self-identify",
            "hispanic": "Decline to self-identify",
        }
        
        for pattern, answer in pattern_answers.items():
            if pattern in q_lower and answer:
                return answer
        
        # Check cached answers
        q_hash = hash(q_lower) % 10000000
        if str(q_hash) in self.answers:
            return self.answers[str(q_hash)]
        
        return None
    
    def save_answer(self, question: str, answer: str):
        """Cache an answer for future use."""
        q_hash = hash(question.lower()) % 10000000
        self.answers[str(q_hash)] = answer
        self.save()


class UniversalJobAgent:
    """
    AI-powered agent that can navigate any job site and fill any form.
    
    Usage:
        agent = UniversalJobAgent(browser_page, profile_path)
        
        # Find jobs
        jobs = agent.discover_jobs("https://metacareers.com/jobs", ["TPM", "Product Manager"])
        
        # Apply to a job
        result = agent.apply_to_job("https://...", auto_submit=False)
    """
    
    def __init__(
        self, 
        page,  # Playwright page
        profile_path: Path = None,
        vision_provider: VisionProvider = None,
        max_steps: int = 50,
        human_in_loop: bool = True
    ):
        self.page = page
        self.vision = vision_provider or LLaVAProvider()
        self.knowledge = KnowledgeBase(profile_path)
        self.max_steps = max_steps
        self.human_in_loop = human_in_loop
        self.state: Optional[AgentState] = None
        self.screenshots_dir = Path(__file__).parent.parent / "screenshots"
        self.screenshots_dir.mkdir(exist_ok=True)
    
    def _screenshot(self, name: str = None) -> bytes:
        """Take screenshot and optionally save to file."""
        screenshot = self.page.screenshot(type="png")
        
        if name:
            path = self.screenshots_dir / f"{name}.png"
            with open(path, "wb") as f:
                f.write(screenshot)
        
        return screenshot
    
    def _analyze_page(self, screenshot: bytes, context: str = "") -> PageAnalysis:
        """Use AI to analyze what's on screen."""
        
        prompt = f"""Analyze this webpage screenshot for a job application agent.

Context: {context}

Identify:
1. PAGE TYPE: Is this a careers home, job listing, job detail, application form, login page, success page, or error?

2. KEY ELEMENTS: List interactive elements (buttons, links, form fields) with their text/labels.

3. SUGGESTED ACTION: What should the agent do next?

Respond in this exact JSON format:
{{
    "page_type": "job_listing|job_detail|application|login_required|success|error|unknown",
    "elements": [
        {{"type": "button|link|input|select|checkbox", "text": "...", "purpose": "..."}}
    ],
    "suggested_action": {{
        "action": "click|type|scroll|wait|done",
        "target": "description of what to click/fill",
        "value": "value to type if applicable",
        "reason": "why this action"
    }},
    "confidence": 0.0-1.0
}}

ONLY output valid JSON, no other text."""

        response = self.vision.analyze(screenshot, prompt)
        
        # Parse response
        try:
            # Find JSON in response
            import re
            json_match = re.search(r'\{[\s\S]*\}', response)
            if json_match:
                data = json.loads(json_match.group())
                return PageAnalysis(
                    page_type=PageType(data.get("page_type", "unknown")),
                    elements=data.get("elements", []),
                    suggested_action=data.get("suggested_action"),
                    raw_response=response,
                    confidence=data.get("confidence", 0.5)
                )
        except (json.JSONDecodeError, ValueError) as e:
            print(f"Failed to parse AI response: {e}")
        
        return PageAnalysis(
            page_type=PageType.UNKNOWN,
            elements=[],
            suggested_action=None,
            raw_response=response
        )
    
    def _find_element(self, description: str) -> Optional[Any]:
        """Find element on page matching description."""
        # Try common selectors based on description
        desc_lower = description.lower()
        
        selectors_to_try = []
        
        if "apply" in desc_lower:
            selectors_to_try = [
                "button:has-text('Apply')",
                "a:has-text('Apply')",
                "[data-testid*='apply']",
                ".apply-button",
            ]
        elif "search" in desc_lower or "filter" in desc_lower:
            selectors_to_try = [
                "input[type='search']",
                "input[placeholder*='search' i]",
                "input[placeholder*='filter' i]",
            ]
        elif "submit" in desc_lower:
            selectors_to_try = [
                "button[type='submit']",
                "button:has-text('Submit')",
                "input[type='submit']",
            ]
        
        # Try each selector
        for selector in selectors_to_try:
            try:
                el = self.page.query_selector(selector)
                if el and el.is_visible():
                    return el
            except:
                continue
        
        # Fallback: try to find by text content
        try:
            el = self.page.query_selector(f"text={description}")
            if el and el.is_visible():
                return el
        except:
            pass
        
        return None
    
    def _execute_action(self, action: AgentAction) -> bool:
        """Execute an action on the page."""
        try:
            if action.action_type == ActionType.CLICK:
                el = self._find_element(action.target)
                if el:
                    el.click()
                    time.sleep(1)
                    return True
                    
            elif action.action_type == ActionType.TYPE:
                el = self._find_element(action.target)
                if el:
                    el.fill(action.value)
                    return True
                    
            elif action.action_type == ActionType.SCROLL:
                self.page.evaluate("window.scrollBy(0, 500)")
                time.sleep(0.5)
                return True
                
            elif action.action_type == ActionType.WAIT:
                time.sleep(2)
                return True
                
        except Exception as e:
            print(f"Action failed: {e}")
        
        return False
    
    def discover_jobs(
        self, 
        careers_url: str, 
        keywords: List[str],
        max_jobs: int = 20
    ) -> List[Dict]:
        """
        Discover jobs on any careers site.
        
        Args:
            careers_url: URL of careers/jobs page
            keywords: Keywords to search for (e.g., ["TPM", "Product Manager"])
            max_jobs: Maximum number of jobs to return
            
        Returns:
            List of job dicts: {title, url, location, company}
        """
        self.state = AgentState(
            url=careers_url,
            goal=f"Find jobs matching: {', '.join(keywords)}"
        )
        
        print(f"\n🔍 Discovering jobs at {careers_url}")
        print(f"   Keywords: {keywords}")
        
        self.page.goto(careers_url, wait_until="domcontentloaded")
        time.sleep(3)
        
        jobs = []
        
        for step in range(self.max_steps):
            self.state.step = step
            
            # Screenshot
            screenshot = self._screenshot(f"discover_step_{step}")
            
            # Analyze
            analysis = self._analyze_page(
                screenshot, 
                f"Looking for jobs matching: {keywords}. Found {len(jobs)} so far."
            )
            
            print(f"\n   Step {step}: {analysis.page_type.value}")
            print(f"   AI says: {analysis.suggested_action}")
            
            # Extract jobs if on listing page
            if analysis.page_type == PageType.JOB_LISTING:
                # TODO: Extract job data from page
                pass
            
            # Execute suggested action
            if analysis.suggested_action:
                action = AgentAction(
                    action_type=ActionType(analysis.suggested_action.get("action", "wait")),
                    target=analysis.suggested_action.get("target", ""),
                    value=analysis.suggested_action.get("value", ""),
                    reason=analysis.suggested_action.get("reason", "")
                )
                
                if action.action_type == ActionType.DONE:
                    break
                    
                self._execute_action(action)
            
            if len(jobs) >= max_jobs:
                break
        
        return jobs
    
    def apply_to_job(
        self, 
        job_url: str,
        auto_submit: bool = False
    ) -> Dict:
        """
        Apply to a job by filling out the application form.
        
        Args:
            job_url: URL of job posting or application
            auto_submit: If True, submit automatically. If False, pause before submit.
            
        Returns:
            Result dict with status, filled fields, etc.
        """
        self.state = AgentState(
            url=job_url,
            goal="Fill out job application form"
        )
        
        result = {
            "success": False,
            "url": job_url,
            "fields_filled": [],
            "fields_unknown": [],
            "screenshots": [],
            "status": "started"
        }
        
        print(f"\n📝 Applying to job: {job_url}")
        
        self.page.goto(job_url, wait_until="domcontentloaded")
        time.sleep(3)
        
        for step in range(self.max_steps):
            self.state.step = step
            
            # Screenshot
            screenshot_name = f"apply_step_{step}"
            screenshot = self._screenshot(screenshot_name)
            result["screenshots"].append(screenshot_name)
            
            # Analyze
            analysis = self._analyze_page(
                screenshot,
                f"Filling job application. Step {step}."
            )
            
            print(f"\n   Step {step}: {analysis.page_type.value}")
            
            # Handle different page types
            if analysis.page_type == PageType.SUCCESS:
                result["success"] = True
                result["status"] = "submitted"
                break
                
            elif analysis.page_type == PageType.ERROR:
                result["status"] = "error"
                break
                
            elif analysis.page_type == PageType.APPLICATION_FORM:
                # Fill form fields
                for element in analysis.elements:
                    if element.get("type") in ("input", "select", "checkbox"):
                        label = element.get("text", "")
                        answer = self.knowledge.find_answer(label)
                        
                        if answer:
                            result["fields_filled"].append({
                                "label": label,
                                "value": answer
                            })
                            # TODO: Actually fill the field
                        else:
                            result["fields_unknown"].append(label)
            
            # Execute suggested action
            if analysis.suggested_action:
                action_type = analysis.suggested_action.get("action", "")
                
                # Pause before submit if not auto
                if "submit" in action_type.lower() and not auto_submit:
                    print("\n⏸️  Ready to submit. Review and confirm.")
                    result["status"] = "ready_to_submit"
                    break
                
                action = AgentAction(
                    action_type=ActionType(action_type) if action_type in [a.value for a in ActionType] else ActionType.WAIT,
                    target=analysis.suggested_action.get("target", ""),
                    value=analysis.suggested_action.get("value", ""),
                )
                
                if action.action_type == ActionType.DONE:
                    break
                    
                self._execute_action(action)
            
            time.sleep(1)
        
        return result


# Convenience function
def create_agent(headless: bool = True) -> tuple:
    """
    Create agent with browser.
    
    Returns:
        (agent, browser_context) - remember to close context when done
    """
    from playwright.sync_api import sync_playwright
    
    playwright = sync_playwright().start()
    browser = playwright.chromium.launch(headless=headless)
    context = browser.new_context(viewport={"width": 1400, "height": 900})
    page = context.new_page()
    
    profile_path = Path(__file__).parent / "profiles" / "anton_tpm.json"
    
    agent = UniversalJobAgent(
        page=page,
        profile_path=profile_path,
        human_in_loop=True
    )
    
    return agent, playwright, browser
