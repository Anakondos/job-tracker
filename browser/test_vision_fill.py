#!/usr/bin/env python3
"""
Test AI Vision (LLaVA) for form field filling.

Тестируем по одному полю снизу вверх.
Логируем все ответы от LLaVA для отладки.
"""

import sys
import time
import json
import base64
import requests
from pathlib import Path

# Add parent dir to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from browser.client import BrowserClient
from browser.config import SCREENSHOTS_DIR


# LLaVA configuration
OLLAMA_URL = "http://localhost:11434"
VISION_MODEL = "llava"  # LLaVA model for vision tasks


def encode_image_to_base64(image_path: str) -> str:
    """Encode image file to base64 string."""
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def ask_llava(prompt: str, image_path: str, model: str = VISION_MODEL) -> dict:
    """
    Send image + prompt to LLaVA and get response.
    
    Returns:
        dict with 'response' and 'raw' fields
    """
    print(f"\n{'='*60}")
    print(f"🤖 LLAVA REQUEST")
    print(f"{'='*60}")
    print(f"📸 Image: {image_path}")
    print(f"📝 Prompt: {prompt[:200]}...")
    print(f"{'='*60}")
    
    image_b64 = encode_image_to_base64(image_path)
    
    payload = {
        "model": model,
        "prompt": prompt,
        "images": [image_b64],
        "stream": False,
    }
    
    try:
        start = time.time()
        response = requests.post(
            f"{OLLAMA_URL}/api/generate",
            json=payload,
            timeout=120,  # LLaVA can be slow
        )
        elapsed = time.time() - start
        
        if response.ok:
            result = response.json()
            llava_response = result.get("response", "")
            
            print(f"\n{'='*60}")
            print(f"🤖 LLAVA RESPONSE (took {elapsed:.1f}s)")
            print(f"{'='*60}")
            print(llava_response)
            print(f"{'='*60}\n")
            
            return {
                "success": True,
                "response": llava_response,
                "elapsed": elapsed,
                "raw": result,
            }
        else:
            print(f"❌ LLaVA error: {response.status_code} - {response.text}")
            return {
                "success": False,
                "error": response.text,
            }
            
    except Exception as e:
        print(f"❌ LLaVA exception: {e}")
        return {
            "success": False,
            "error": str(e),
        }


def analyze_form_field(image_path: str, field_description: str) -> dict:
    """
    Ask LLaVA to analyze a specific form field from screenshot.
    
    Args:
        image_path: Path to screenshot
        field_description: Description of field to find (e.g., "Gender dropdown")
        
    Returns:
        dict with field info
    """
    prompt = f"""Look at this job application form screenshot.

Find the field labeled: "{field_description}"

Tell me:
1. Is this field visible in the screenshot? (yes/no)
2. What type of field is it? (text input, dropdown, checkbox, radio button)
3. What is the exact label text shown?
4. What are the current options if it's a dropdown/select?
5. What value is currently selected (if any)?

Be specific and concise. Only describe what you actually see."""

    return ask_llava(prompt, image_path)


def get_field_selector(image_path: str, field_label: str) -> dict:
    """
    Ask LLaVA to help identify how to locate a field for automation.
    """
    prompt = f"""Look at this job application form screenshot.

I need to automate filling the field labeled "{field_label}".

Based on what you see:
1. Describe the field's position (top/middle/bottom of visible area)
2. What text or label is directly above or beside it?
3. Is it a standard dropdown or a custom autocomplete field?
4. What would you suggest clicking to interact with this field?

Be specific about visual landmarks that could help locate this field programmatically."""

    return ask_llava(prompt, image_path)


def test_fill_single_field(browser: BrowserClient, field_name: str, expected_value: str):
    """
    Test filling a single field using AI vision guidance.
    
    Args:
        browser: BrowserClient instance
        field_name: Human-readable field name (e.g., "Gender")
        expected_value: Value to fill (e.g., "Decline to self-identify")
    """
    print(f"\n{'#'*60}")
    print(f"# TESTING FIELD: {field_name}")
    print(f"# Expected value: {expected_value}")
    print(f"{'#'*60}")
    
    # Step 1: Take screenshot of current state
    screenshot_path = browser.screenshot(f"test_{field_name.lower().replace(' ', '_')}_before.png")
    
    # Step 2: Ask LLaVA to analyze the field
    analysis = analyze_form_field(str(screenshot_path), field_name)
    
    if not analysis.get("success"):
        print(f"❌ Failed to analyze field: {analysis.get('error')}")
        return False
    
    # Step 3: Get selector guidance
    selector_info = get_field_selector(str(screenshot_path), field_name)
    
    # Step 4: Based on analysis, try to fill
    print(f"\n📋 Analysis complete. Attempting to fill...")
    
    # For now, return the analysis for manual review
    return {
        "field": field_name,
        "analysis": analysis.get("response"),
        "selector_info": selector_info.get("response"),
    }


def main():
    """Main test flow."""
    
    # Test URL (Coinbase Greenhouse form)
    url = "https://job-boards.greenhouse.io/embed/job_app?token=7404427&for=coinbase&gh_jid=7404427"
    
    print("\n" + "="*60)
    print("🧪 AI VISION FORM FILLING TEST")
    print("="*60)
    print(f"URL: {url}")
    print(f"Model: {VISION_MODEL}")
    print("="*60 + "\n")
    
    # First, check if Ollama is running
    try:
        r = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
        if r.ok:
            models = [m["name"] for m in r.json().get("models", [])]
            print(f"✅ Ollama is running. Available models: {models}")
            if VISION_MODEL not in [m.split(":")[0] for m in models]:
                print(f"⚠️ Model {VISION_MODEL} not found. Run: ollama pull {VISION_MODEL}")
                return
        else:
            print("❌ Ollama not responding correctly")
            return
    except Exception as e:
        print(f"❌ Cannot connect to Ollama at {OLLAMA_URL}: {e}")
        print("   Run: ollama serve")
        return
    
    # Start browser and navigate
    with BrowserClient(headless=False) as browser:
        print("\n🌐 Opening page...")
        if not browser.open_job_page(url):
            print("❌ Failed to open page")
            return
        
        # Wait for form to load
        time.sleep(3)
        
        # Scroll to bottom to see demographic fields
        print("📜 Scrolling to bottom of form...")
        browser.scroll_to_bottom()
        time.sleep(1)
        
        # Take initial screenshot
        initial_screenshot = browser.screenshot("coinbase_bottom_initial.png")
        print(f"📸 Initial screenshot: {initial_screenshot}")
        
        # Test fields from bottom to top
        # Start with one field - Gender dropdown
        print("\n" + "="*60)
        print("TESTING: Gender dropdown (bottom of form)")
        print("="*60)
        
        result = test_fill_single_field(
            browser, 
            "Gender", 
            "Decline to self-identify"
        )
        
        if result:
            print("\n📋 TEST RESULTS:")
            print(json.dumps(result, indent=2, ensure_ascii=False))
        
        # Wait for user to verify
        print("\n" + "="*60)
        print("⏸️  PAUSED FOR VERIFICATION")
        print("Check the browser and console output above.")
        print("Press Enter to continue or Ctrl+C to stop...")
        print("="*60)
        
        try:
            input()
        except KeyboardInterrupt:
            print("\n\n👋 Test stopped by user")
            return
        
        print("\n✅ Test complete!")


if __name__ == "__main__":
    main()
