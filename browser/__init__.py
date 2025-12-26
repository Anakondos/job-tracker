"""
Browser automation module for job applications.

Usage:
    from browser import BrowserClient
    
    with BrowserClient() as browser:
        browser.open_job_page("https://...")
        browser.screenshot("step1.png")
        browser.find_and_click_apply()
"""

from .client import BrowserClient
from .config import SCREENSHOTS_DIR, AI_CONFIG

__all__ = ["BrowserClient", "SCREENSHOTS_DIR", "AI_CONFIG"]
