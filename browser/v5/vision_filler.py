"""
Vision-based Form Filler
Uses Claude Vision API to analyze form screenshots and fill fields.
"""

import asyncio
import base64
import os
import json
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from dotenv import load_dotenv

# Load environment
load_dotenv(Path(__file__).parent.parent.parent / ".env")

import anthropic
from playwright.async_api import Page, Frame

class VisionFormFiller:
    """Uses Claude Vision to analyze and fill forms."""
    
    def __init__(self, profile_data: Dict = None):
        self.client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        self.profile = profile_data or self._load_default_profile()
        
    def _load_default_profile(self) -> Dict:
        """Load default profile from file."""
        profile_path = Path(__file__).parent.parent / "profiles" / "anton_kondakov.json"
        if profile_path.exists():
            with open(profile_path) as f:
                return json.load(f)
        return {}
    
    async def analyze_form(self, page: Page, num_screenshots: int = 3) -> str:
        """Take screenshots and analyze form with Vision API."""
        
        # Set reasonable viewport
        await page.set_viewport_size({"width": 1200, "height": 900})
        await asyncio.sleep(0.5)
        
        # Scroll to top
        await page.evaluate("window.scrollTo(0, 0)")
        await asyncio.sleep(0.5)
        
        # Take screenshots while scrolling
        screenshots = []
        for i in range(num_screenshots):
            ss = await page.screenshot(full_page=False)
            screenshots.append(ss)
            await page.evaluate("window.scrollBy(0, 800)")
            await asyncio.sleep(0.3)
        
        # Build Vision API request
        content = []
        for i, ss in enumerate(screenshots):
            content.append({
                "type": "text",
                "text": f"Screenshot {i+1}/{num_screenshots}:"
            })
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": base64.standard_b64encode(ss).decode("utf-8"),
                },
            })
        
        # Add analysis prompt
        profile_text = self._format_profile()
        content.append({
            "type": "text",
            "text": f"""Analyze this job application form.

1. List ALL visible form fields with:
   - field_id: CSS selector or descriptive ID
   - label: field label text
   - type: text/dropdown/checkbox/file/radio/textarea
   - required: true/false
   
2. For each field, provide the VALUE to fill based on this profile:
{profile_text}

Return as JSON:
{{
  "fields": [
    {{"field_id": "first_name", "label": "First Name", "type": "text", "required": true, "value": "Anton"}},
    ...
  ]
}}"""
        })
        
        # Call Vision API
        message = self.client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4000,
            messages=[{"role": "user", "content": content}],
        )
        
        return message.content[0].text
    
    def _format_profile(self) -> str:
        """Format profile data for prompt."""
        p = self.profile.get("personal", {})
        w = self.profile.get("work_experience", [{}])[0] if self.profile.get("work_experience") else {}
        e = self.profile.get("education", [{}])[0] if self.profile.get("education") else {}
        
        return f"""
- First Name: {p.get('first_name', 'Anton')}
- Last Name: {p.get('last_name', 'Kondakov')}
- Email: {p.get('email', 'anakondos@gmail.com')}
- Phone: {p.get('phone', '+1 910 536 0602')}
- Location: {p.get('location', 'Raleigh, NC')}
- Country: United States
- Current Employer: {w.get('company', 'DXC Technology')}
- Job Title: {w.get('title', 'Solution Architect')}
- School: {e.get('school', 'Moscow Institute of Physics and Technology')}
- Degree: {e.get('degree', 'Master of Science')}
- US Work Authorization: Yes
- Needs Visa Sponsorship: No
- Remote Work: Yes
- Previously at this company: No
- WhatsApp Opt-in: No
- LinkedIn: {self.profile.get('links', {}).get('linkedin', '')}"""

    async def fill_form(self, page: Page, field_instructions: List[Dict]) -> Dict:
        """Fill form fields based on Vision analysis."""
        results = {"filled": 0, "failed": 0, "skipped": 0}
        
        for field in field_instructions:
            field_id = field.get("field_id", "")
            value = field.get("value", "")
            field_type = field.get("type", "text")
            
            if not value:
                results["skipped"] += 1
                continue
            
            try:
                # Try different selectors
                selectors = [
                    f"#{field_id}",
                    f"input[id='{field_id}']",
                    f"input[name='{field_id}']",
                    f"input[aria-label*='{field.get('label', '')}']",
                    f"input[placeholder*='{field.get('label', '')}']",
                ]
                
                el = None
                for sel in selectors:
                    try:
                        el = await page.query_selector(sel)
                        if el and await el.is_visible():
                            break
                        el = None
                    except:
                        continue
                
                if not el:
                    # Try in frames
                    for frame in page.frames:
                        for sel in selectors:
                            try:
                                el = await frame.query_selector(sel)
                                if el and await el.is_visible():
                                    break
                                el = None
                            except:
                                continue
                        if el:
                            break
                
                if el:
                    if field_type == "checkbox":
                        if value.lower() in ["yes", "true", "1"]:
                            await el.check()
                        else:
                            await el.uncheck()
                    elif field_type == "dropdown":
                        await el.click()
                        await asyncio.sleep(0.3)
                        option = await page.query_selector(f"li:has-text('{value}')")
                        if option:
                            await option.click()
                    else:
                        await el.fill(str(value))
                    
                    results["filled"] += 1
                    print(f"✅ {field_id}: {value[:30]}")
                else:
                    results["failed"] += 1
                    print(f"❌ {field_id}: element not found")
                    
            except Exception as e:
                results["failed"] += 1
                print(f"❌ {field_id}: {str(e)[:50]}")
        
        return results


async def test_vision_filler():
    """Test the vision filler on Stripe form."""
    from playwright.async_api import async_playwright
    
    filler = VisionFormFiller()
    
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://localhost:9222")
        ctx = browser.contexts[0]
        
        # Create new page
        page = await ctx.new_page()
        await page.goto(
            "https://stripe.com/jobs/listing/product-manager-card-costs-and-settlement/7176532/apply",
            wait_until="domcontentloaded",
            timeout=30000
        )
        await asyncio.sleep(6)
        
        print("Analyzing form with Vision API...")
        analysis = await filler.analyze_form(page)
        print(analysis)


if __name__ == "__main__":
    asyncio.run(test_vision_filler())
