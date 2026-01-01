#!/usr/bin/env python3
"""
Test Universal Agent on Meta Careers.

This tests the AI-driven approach on a non-standard ATS.
"""

import sys
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from browser.universal_agent import UniversalJobAgent, LLaVAProvider, create_agent


def test_meta_discovery():
    """Test discovering jobs on Meta Careers."""
    
    print("\n" + "="*70)
    print("🤖 UNIVERSAL AGENT TEST - Meta Careers")
    print("="*70)
    
    agent, playwright, browser = create_agent(headless=False)
    
    try:
        # Test job discovery
        jobs = agent.discover_jobs(
            careers_url="https://www.metacareers.com/jobs",
            keywords=["Product Manager", "TPM", "Technical Program Manager"],
            max_jobs=10
        )
        
        print(f"\n📋 Found {len(jobs)} jobs:")
        for job in jobs[:10]:
            print(f"   - {job.get('title', 'Unknown')}")
        
    finally:
        browser.close()
        playwright.stop()
    
    print("\n✅ Test complete!")


def test_meta_application():
    """Test applying to a specific Meta job."""
    
    job_url = "https://www.metacareers.com/jobs/1296230207698571"
    
    print("\n" + "="*70)
    print("🤖 UNIVERSAL AGENT TEST - Meta Application")
    print("="*70)
    print(f"URL: {job_url}")
    
    agent, playwright, browser = create_agent(headless=False)
    
    try:
        result = agent.apply_to_job(
            job_url=job_url,
            auto_submit=False  # Don't actually submit
        )
        
        print(f"\n📋 Result:")
        print(json.dumps(result, indent=2))
        
    finally:
        browser.close()
        playwright.stop()
    
    print("\n✅ Test complete!")


if __name__ == "__main__":
    # Run application test by default
    test_meta_application()
