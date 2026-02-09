"""
V8 Form Filler Agent - Based on Anthropic Computer Use API

Uses official computer_20250124 tool for autonomous form filling.
This is the most powerful version - can handle ANY form.
"""

import os
import json
import base64
import time
import asyncio
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any, List

import anthropic
from playwright.async_api import async_playwright, Page, Browser

# Constants
DISPLAY_WIDTH = 1280
DISPLAY_HEIGHT = 900
MAX_ITERATIONS = 50
DEFAULT_TIMEOUT = 60000


class V8Agent:
    """
    V8 Form Filler using Anthropic Computer Use API.
    
    Flow:
    1. Navigate to job application URL
    2. Take screenshot → send to Claude with task
    3. Claude returns action (click, type, scroll, etc.)
    4. Execute action via Playwright
    5. Take new screenshot → send back to Claude
    6. Repeat until form is complete
    """
    
    def __init__(self, profile_path: str = None, debug: bool = True):
        self.debug = debug
        self.client = anthropic.Anthropic()
        self.browser: Optional[Browser] = None
        self.page: Optional[Page] = None
        self.messages: List[Dict] = []
        self.iteration = 0
        self.screenshots_dir = Path("browser/v8/screenshots")
        self.screenshots_dir.mkdir(parents=True, exist_ok=True)
        
        # Load profile
        self.profile = self._load_profile(profile_path)
        self.answer_library = self._load_answer_library()
        
    def _load_profile(self, profile_path: str = None) -> Dict:
        """Load candidate profile."""
        if not profile_path:
            profile_path = "browser/profiles/anton_tpm.json"
        
        path = Path(profile_path)
        if path.exists():
            with open(path) as f:
                return json.load(f)
        return {}
    
    def _load_answer_library(self) -> Dict:
        """Load answer library for common questions."""
        lib_path = Path("data/answer_library.json")
        if lib_path.exists():
            with open(lib_path) as f:
                return json.load(f)
        return {}
    
    def _log(self, msg: str):
        """Debug logging."""
        if self.debug:
            timestamp = datetime.now().strftime("%H:%M:%S")
            print(f"[V8 {timestamp}] {msg}")
    
    async def connect_to_chrome(self, port: int = 9222) -> bool:
        """Connect to existing Chrome debug instance."""
        try:
            playwright = await async_playwright().start()
            self.browser = await playwright.chromium.connect_over_cdp(f"http://localhost:{port}")
            
            # Get existing page or create new
            contexts = self.browser.contexts
            if contexts and contexts[0].pages:
                self.page = contexts[0].pages[0]
            else:
                context = await self.browser.new_context()
                self.page = await context.new_page()
            
            await self.page.set_viewport_size({"width": DISPLAY_WIDTH, "height": DISPLAY_HEIGHT})
            self._log(f"Connected to Chrome on port {port}")
            return True
            
        except Exception as e:
            self._log(f"Failed to connect: {e}")
            return False
    
    async def take_screenshot(self) -> str:
        """Take screenshot and return as base64."""
        if not self.page:
            return ""
        
        screenshot_bytes = await self.page.screenshot(type="png")
        
        # Save for debugging
        if self.debug:
            path = self.screenshots_dir / f"step_{self.iteration:03d}.png"
            with open(path, "wb") as f:
                f.write(screenshot_bytes)
            self._log(f"Screenshot saved: {path}")
        
        return base64.standard_b64encode(screenshot_bytes).decode("utf-8")
    
    async def execute_action(self, action: Dict) -> str:
        """Execute computer use action via Playwright."""
        action_type = action.get("action")
        self._log(f"Executing: {action_type} - {action}")
        
        try:
            if action_type == "screenshot":
                # Just return current screenshot
                return "Screenshot captured"
            
            elif action_type == "left_click":
                x, y = action.get("coordinate", [0, 0])
                await self.page.mouse.click(x, y)
                await asyncio.sleep(0.3)
                return f"Clicked at ({x}, {y})"
            
            elif action_type == "double_click":
                x, y = action.get("coordinate", [0, 0])
                await self.page.mouse.dblclick(x, y)
                await asyncio.sleep(0.3)
                return f"Double-clicked at ({x}, {y})"
            
            elif action_type == "right_click":
                x, y = action.get("coordinate", [0, 0])
                await self.page.mouse.click(x, y, button="right")
                await asyncio.sleep(0.3)
                return f"Right-clicked at ({x}, {y})"
            
            elif action_type == "type":
                text = action.get("text", "")
                await self.page.keyboard.type(text, delay=20)
                await asyncio.sleep(0.2)
                return f"Typed: {text[:50]}..."
            
            elif action_type == "key":
                key = action.get("text", "")
                # Map common key names
                key_map = {
                    "Return": "Enter",
                    "Tab": "Tab",
                    "Escape": "Escape",
                    "BackSpace": "Backspace",
                    "Delete": "Delete",
                    "space": " ",
                    "ctrl+a": "Control+a",
                    "ctrl+c": "Control+c",
                    "ctrl+v": "Control+v",
                }
                key = key_map.get(key, key)
                await self.page.keyboard.press(key)
                await asyncio.sleep(0.2)
                return f"Pressed key: {key}"
            
            elif action_type == "scroll":
                x, y = action.get("coordinate", [DISPLAY_WIDTH // 2, DISPLAY_HEIGHT // 2])
                direction = action.get("scroll_direction", "down")
                amount = action.get("scroll_amount", 3)
                
                delta_y = -100 * amount if direction == "up" else 100 * amount
                delta_x = -100 * amount if direction == "left" else 100 * amount if direction == "right" else 0
                
                if direction in ["up", "down"]:
                    await self.page.mouse.wheel(0, delta_y)
                else:
                    await self.page.mouse.wheel(delta_x, 0)
                
                await asyncio.sleep(0.3)
                return f"Scrolled {direction} by {amount}"
            
            elif action_type == "mouse_move":
                x, y = action.get("coordinate", [0, 0])
                await self.page.mouse.move(x, y)
                return f"Moved mouse to ({x}, {y})"
            
            elif action_type == "left_click_drag":
                start = action.get("start_coordinate", [0, 0])
                end = action.get("coordinate", [0, 0])
                await self.page.mouse.move(start[0], start[1])
                await self.page.mouse.down()
                await self.page.mouse.move(end[0], end[1])
                await self.page.mouse.up()
                return f"Dragged from {start} to {end}"
            
            elif action_type == "wait":
                duration = action.get("duration", 1)
                await asyncio.sleep(duration)
                return f"Waited {duration} seconds"
            
            else:
                return f"Unknown action: {action_type}"
                
        except Exception as e:
            self._log(f"Action error: {e}")
            return f"Error: {str(e)}"
    
    def _build_system_prompt(self, job_info: Dict) -> str:
        """Build system prompt with candidate profile and instructions."""
        
        profile_text = f"""
CANDIDATE PROFILE:
- Name: {self.profile.get('name', 'Anton Kondakov')}
- Email: {self.profile.get('email', 'anton.kondakov.PM@gmail.com')}
- Phone: {self.profile.get('phone', '(910) 536-0602')}
- Location: {self.profile.get('location', 'Raleigh, NC')}
- LinkedIn: {self.profile.get('linkedin', 'linkedin.com/in/antonkondakov')}

WORK AUTHORIZATION:
- Authorized to work in US: Yes
- Requires sponsorship: No
- Willing to relocate: Yes

DEMOGRAPHICS (for voluntary questions):
- Gender: Male
- Race/Ethnicity: White / Caucasian
- Veteran status: No
- Disability: No

SALARY EXPECTATIONS:
- Desired: $180,000 - $220,000
- Minimum: $160,000
"""

        job_text = f"""
JOB BEING APPLIED TO:
- Company: {job_info.get('company', 'Unknown')}
- Position: {job_info.get('title', 'Unknown')}
- URL: {job_info.get('url', '')}
"""

        return f"""You are an expert form-filling assistant helping to complete a job application.

{profile_text}

{job_text}

INSTRUCTIONS:
1. Fill out the job application form completely and accurately
2. Use the candidate profile information provided above
3. For multiple choice questions, select the most appropriate option
4. For voluntary demographic questions (race, gender, disability, veteran), answer honestly or select "Decline to answer" if preferred
5. Upload resume when prompted (the file is already prepared)
6. DO NOT submit the form - stop when you reach the final Submit button
7. After each action, verify the result before proceeding

IMPORTANT RULES:
- Click on input fields before typing
- For dropdowns, click to open then click the correct option
- Scroll down to see more of the form if needed
- If a field is already filled correctly, skip it
- If you make a mistake, try to correct it
- Take your time - accuracy is more important than speed

When you have filled all visible fields and scrolled to check for more, 
and you see the final Submit button, STOP and report "Form filling complete. Ready for review."
"""

    async def fill_form(self, job_url: str, job_info: Dict = None) -> Dict:
        """
        Main entry point - fill a job application form.
        
        Args:
            job_url: URL of the job application
            job_info: Optional dict with company, title, etc.
        
        Returns:
            Dict with status and details
        """
        if not job_info:
            job_info = {"url": job_url}
        
        self._log(f"Starting form fill: {job_url}")
        
        # Check if already on the page (skip navigation)
        current_url = self.page.url
        if job_url not in current_url and "greenhouse" in job_url and "greenhouse" not in current_url:
            # Navigate to the form
            try:
                await self.page.goto(job_url, wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(3)  # Wait for dynamic content
            except Exception as e:
                return {"ok": False, "error": f"Failed to load page: {e}"}
        else:
            self._log("Already on target page, skipping navigation")
            await asyncio.sleep(1)
        
        # Initialize conversation
        system_prompt = self._build_system_prompt(job_info)
        
        # Take initial screenshot
        screenshot_b64 = await self.take_screenshot()
        
        # Initial message
        self.messages = [{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": screenshot_b64
                    }
                },
                {
                    "type": "text",
                    "text": "Please fill out this job application form. Start by analyzing what fields are visible and begin filling them in order."
                }
            ]
        }]
        
        # Agent loop
        self.iteration = 0
        last_action = None
        consecutive_screenshots = 0
        
        while self.iteration < MAX_ITERATIONS:
            self.iteration += 1
            self._log(f"=== Iteration {self.iteration} ===")
            
            try:
                # Call Claude with computer use tool
                response = self.client.beta.messages.create(
                    model="claude-sonnet-4-20250514",
                    max_tokens=4096,
                    system=system_prompt,
                    tools=[
                        {
                            "type": "computer_20250124",
                            "name": "computer",
                            "display_width_px": DISPLAY_WIDTH,
                            "display_height_px": DISPLAY_HEIGHT,
                        }
                    ],
                    messages=self.messages,
                    betas=["computer-use-2025-01-24"]
                )
                
                self._log(f"Response stop_reason: {response.stop_reason}")
                
                # Process response
                assistant_content = []
                tool_results = []
                
                for block in response.content:
                    if block.type == "text":
                        self._log(f"Claude says: {block.text[:200]}...")
                        assistant_content.append({"type": "text", "text": block.text})
                        
                        # Check for completion
                        if "complete" in block.text.lower() and "review" in block.text.lower():
                            self._log("Form filling complete!")
                            return {
                                "ok": True,
                                "message": "Form filled successfully",
                                "iterations": self.iteration,
                                "final_message": block.text
                            }
                    
                    elif block.type == "tool_use":
                        assistant_content.append({
                            "type": "tool_use",
                            "id": block.id,
                            "name": block.name,
                            "input": block.input
                        })
                        
                        # Execute the action
                        action = block.input
                        result = await self.execute_action(action)
                        
                        # Track consecutive screenshots
                        if action.get("action") == "screenshot":
                            consecutive_screenshots += 1
                            if consecutive_screenshots > 3:
                                self._log("Too many consecutive screenshots, might be stuck")
                        else:
                            consecutive_screenshots = 0
                        
                        # Take screenshot after action
                        await asyncio.sleep(0.5)
                        screenshot_b64 = await self.take_screenshot()
                        
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": [
                                {"type": "text", "text": result},
                                {
                                    "type": "image",
                                    "source": {
                                        "type": "base64",
                                        "media_type": "image/png",
                                        "data": screenshot_b64
                                    }
                                }
                            ]
                        })
                        
                        last_action = action
                
                # Add to conversation
                self.messages.append({"role": "assistant", "content": assistant_content})
                
                if tool_results:
                    self.messages.append({"role": "user", "content": tool_results})
                
                # Check if no tool use - might be done or stuck
                if response.stop_reason == "end_turn" and not tool_results:
                    self._log("Claude stopped without tool use")
                    final_text = next((b.text for b in response.content if hasattr(b, 'text')), "")
                    return {
                        "ok": True,
                        "message": "Form filling ended",
                        "iterations": self.iteration,
                        "final_message": final_text
                    }
                    
            except Exception as e:
                self._log(f"Error in iteration: {e}")
                return {"ok": False, "error": str(e), "iteration": self.iteration}
        
        return {
            "ok": False,
            "error": "Max iterations reached",
            "iterations": self.iteration
        }
    
    async def close(self):
        """Clean up resources."""
        if self.browser:
            await self.browser.close()


async def main():
    """Test the V8 agent."""
    import sys
    
    # Get URL from command line or use default test URL
    if len(sys.argv) > 1:
        job_url = sys.argv[1]
    else:
        # Default test URL (Greenhouse)
        job_url = "https://boards.greenhouse.io/abnormalsecurity/jobs/6252824003"
    
    agent = V8Agent(debug=True)
    
    # Connect to Chrome
    if not await agent.connect_to_chrome(9222):
        print("Failed to connect to Chrome. Make sure it's running with --remote-debugging-port=9222")
        return
    
    # Fill the form
    result = await agent.fill_form(
        job_url,
        job_info={
            "company": "Test Company",
            "title": "Test Position",
            "url": job_url
        }
    )
    
    print("\n" + "="*50)
    print("RESULT:", json.dumps(result, indent=2))
    
    await agent.close()


if __name__ == "__main__":
    asyncio.run(main())
