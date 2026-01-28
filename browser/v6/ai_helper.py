"""
AI Helper for V6 Form Filler
Uses Claude API for intelligent form filling
"""

import json
from pathlib import Path
from typing import Optional, List
import anthropic


def get_api_key() -> Optional[str]:
    """Get Claude API key."""
    import os
    
    # Environment
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        return key
    
    # Config file (V5 location)
    config_path = Path(__file__).parent.parent / "v5" / "config" / "api_keys.json"
    if config_path.exists():
        with open(config_path) as f:
            config = json.load(f)
            return config.get("anthropic_api_key")
    
    return None


class AIHelper:
    """AI assistant for form filling."""
    
    def __init__(self):
        self.api_key = get_api_key()
        self._client = None
        
    @property
    def available(self) -> bool:
        return self.api_key is not None
    
    @property
    def client(self):
        if self._client is None and self.api_key:
            self._client = anthropic.Anthropic(api_key=self.api_key)
        return self._client
    
    def get_answer(
        self, 
        question: str, 
        options: List[str] = None,
        profile_context: str = ""
    ) -> Optional[str]:
        """
        Get answer for a form question using AI.
        
        Args:
            question: The question/label text
            options: Available options for dropdown (None for text field)
            profile_context: User profile info
            
        Returns:
            Answer string or None if failed
        """
        if not self.available:
            return None
        
        try:
            if options:
                # Dropdown - select from options
                prompt = f"""Job application question: {question}

Available options:
{chr(10).join(f'- {opt}' for opt in options)}

Applicant: {profile_context}

Select the BEST option. Return ONLY the exact option text, nothing else."""
            else:
                # Text field
                prompt = f"""Job application question: {question}

Applicant: {profile_context}

Provide a brief, professional answer (1-2 sentences max).
Return ONLY the answer text, nothing else."""

            response = self.client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=100,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
            )
            
            answer = response.content[0].text.strip()
            
            # For dropdowns, verify answer is in options
            if options:
                answer_lower = answer.lower()
                for opt in options:
                    if opt.lower() in answer_lower or answer_lower in opt.lower():
                        return opt
                # If no match, return first option as fallback
                return options[0] if options else answer
            
            return answer
            
        except Exception as e:
            print(f"   ⚠️ AI error: {e}")
            return None
    
    def match_option(self, answer: str, options: List[str]) -> Optional[str]:
        """Find best matching option for an answer."""
        if not options:
            return answer
            
        answer_lower = answer.lower()
        
        # Exact match
        for opt in options:
            if opt.lower() == answer_lower:
                return opt
        
        # Partial match
        for opt in options:
            if answer_lower in opt.lower() or opt.lower() in answer_lower:
                return opt
        
        # Use AI to pick best
        if self.available:
            return self.get_answer(
                f"Select option closest to '{answer}'",
                options=options
            )
        
        return options[0] if options else answer
