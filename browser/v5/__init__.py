"""
Smart Form Filler V5.0 - Production-Ready Universal Form Filler

Key Features:
- Real Chrome browser with your logins/cookies via CDP
- Multi-layer field detection (HTML → Probe → Vision)
- Multi-source answer resolution (Profile → Learned → AI → Human)
- Post-fill validation with retry
- Pre-flight check mode
- ATS-specific adapters
"""

from .engine import FormFillerV5
from .browser_manager import BrowserManager

__all__ = ["FormFillerV5", "BrowserManager"]
