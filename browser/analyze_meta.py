#!/usr/bin/env python3
"""
Analyze Meta Careers page structure (non-interactive).
"""

import sys
import json
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from playwright.sync_api import sync_playwright


def analyze_meta_page():
    """Analyze Meta careers page structure."""
    
    job_url = "https://www.metacareers.com/jobs/1296230207698571"
    
    print("\n" + "="*70)
    print("🔍 ANALYZING META CAREERS PAGE")
    print("="*70)
    print(f"URL: {job_url}")
    print("="*70)
    
    intercepted = {
        "xhr_requests": [],
        "api_responses": [],
        "graphql": [],
    }
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1400, "height": 900})
        page = context.new_page()
        
        # Setup network interception
        def on_request(request):
            if request.resource_type in ("xhr", "fetch"):
                intercepted["xhr_requests"].append({
                    "url": request.url[:100],
                    "method": request.method,
                })
                
                # Check for GraphQL
                if "graphql" in request.url.lower():
                    try:
                        post_data = request.post_data
                        if post_data:
                            intercepted["graphql"].append({
                                "url": request.url,
                                "body": post_data[:500]
                            })
                    except:
                        pass
        
        def on_response(response):
            content_type = response.headers.get("content-type", "")
            if "application/json" in content_type:
                try:
                    url = response.url
                    if any(x in url.lower() for x in ["job", "career", "apply", "graphql", "api"]):
                        body = response.json()
                        intercepted["api_responses"].append({
                            "url": url[:100],
                            "status": response.status,
                            "keys": list(body.keys()) if isinstance(body, dict) else "array",
                            "sample": str(body)[:500]
                        })
                except:
                    pass
        
        page.on("request", on_request)
        page.on("response", on_response)
        
        # Navigate
        print("\n📄 Loading page...")
        try:
            page.goto(job_url, wait_until="networkidle", timeout=30000)
        except:
            page.goto(job_url, wait_until="domcontentloaded", timeout=30000)
        time.sleep(3)
        
        print("\n📡 INTERCEPTED XHR REQUESTS:")
        print("-"*70)
        for req in intercepted["xhr_requests"][:15]:
            print(f"   {req['method']:6} {req['url']}")
        
        if intercepted["graphql"]:
            print("\n🔮 GRAPHQL QUERIES:")
            print("-"*70)
            for gql in intercepted["graphql"][:5]:
                print(f"   {gql['url'][:60]}")
                print(f"   Body: {gql['body'][:200]}...")
        
        if intercepted["api_responses"]:
            print("\n📦 API RESPONSES:")
            print("-"*70)
            for resp in intercepted["api_responses"][:5]:
                print(f"   {resp['url']}")
                print(f"   Keys: {resp['keys']}")
        
        # Page title
        title = page.title()
        print(f"\n📋 Title: {title}")
        
        # Structured data
        job_data = page.evaluate("""() => {
            const scripts = document.querySelectorAll('script[type="application/ld+json"]');
            const data = [];
            scripts.forEach(s => {
                try { data.push(JSON.parse(s.textContent)); } catch(e) {}
            });
            return data;
        }""")
        
        if job_data:
            print("\n📊 STRUCTURED DATA (ld+json):")
            for d in job_data:
                print(f"   Type: {d.get('@type', 'unknown')}")
                if d.get('@type') == 'JobPosting':
                    print(f"   Title: {d.get('title', 'N/A')}")
                    print(f"   Company: {d.get('hiringOrganization', {}).get('name', 'N/A')}")
        
        # Apply button
        print("\n🔘 LOOKING FOR APPLY BUTTON:")
        apply_buttons = page.query_selector_all("a[href*='apply'], button:has-text('Apply'), a:has-text('Apply')")
        for btn in apply_buttons[:5]:
            try:
                text = btn.inner_text().strip()[:50]
                href = btn.get_attribute("href") or "no href"
                print(f"   '{text}' -> {href[:80]}")
            except:
                pass
        
        # Forms and inputs
        forms = page.query_selector_all("form")
        inputs = page.query_selector_all("input:not([type='hidden'])")
        print(f"\n📝 Forms: {len(forms)}, Visible inputs: {len(inputs)}")
        
        for inp in inputs[:10]:
            try:
                inp_type = inp.get_attribute("type") or "text"
                inp_name = inp.get_attribute("name") or inp.get_attribute("id") or "unnamed"
                placeholder = inp.get_attribute("placeholder") or ""
                print(f"   [{inp_type}] {inp_name} - {placeholder[:30]}")
            except:
                pass
        
        # Screenshot
        screenshot_path = PROJECT_ROOT / "screenshots" / "meta_analysis.png"
        page.screenshot(path=str(screenshot_path))
        print(f"\n📸 Screenshot: {screenshot_path}")
        
        browser.close()
    
    print("\n✅ Analysis complete!")
    return intercepted


if __name__ == "__main__":
    analyze_meta_page()
