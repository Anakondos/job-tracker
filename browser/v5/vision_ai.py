"""
Vision AI Module - Claude API for fast, accurate form analysis

Claude Vision API:
- Fast: ~2-5 seconds per image
- Accurate: 95%+ field recognition
- Structured output: JSON responses

Usage:
    from browser.v5.vision_ai import VisionAI
    
    vision = VisionAI()
    
    # Analyze form field
    result = vision.analyze_field(screenshot_path, "What type of field is this?")
    
    # Get field value to fill
    answer = vision.get_field_answer(screenshot_path, "Gender dropdown with options")
"""

import os
import json
import base64
import time
from pathlib import Path
from typing import Optional, Dict, Any, List
from dataclasses import dataclass
import anthropic


# ═══════════════════════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════════════════════

# Get API key from environment or config file
def get_api_key() -> Optional[str]:
    """Get Claude API key from environment or config."""
    # 1. Environment variable
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        return key
    
    # 2. Config file
    config_path = Path(__file__).parent / "config" / "api_keys.json"
    if config_path.exists():
        with open(config_path) as f:
            config = json.load(f)
            return config.get("anthropic_api_key")
    
    # 3. Home directory config
    home_config = Path.home() / ".anthropic" / "api_key"
    if home_config.exists():
        return home_config.read_text().strip()
    
    return None


@dataclass
class VisionConfig:
    """Vision AI configuration."""
    model: str = "claude-sonnet-4-20250514"  # Best balance of speed/quality
    max_tokens: int = 1024
    temperature: float = 0.1  # Low for consistent structured output


# ═══════════════════════════════════════════════════════════════════════════
# VISION AI
# ═══════════════════════════════════════════════════════════════════════════

class VisionAI:
    """
    Claude Vision API for form field analysis.
    
    Fast and accurate vision analysis using Claude API.
    """
    
    def __init__(self, api_key: Optional[str] = None, config: Optional[VisionConfig] = None):
        self.api_key = api_key or get_api_key()
        self.config = config or VisionConfig()
        self._client = None
        
        if not self.api_key:
            print("⚠️ No Claude API key found. Set ANTHROPIC_API_KEY environment variable.")
    
    @property
    def client(self) -> anthropic.Anthropic:
        """Lazy-load Anthropic client."""
        if self._client is None:
            if not self.api_key:
                raise ValueError("Claude API key not configured")
            self._client = anthropic.Anthropic(api_key=self.api_key)
        return self._client
    
    @property
    def available(self) -> bool:
        """Check if Vision AI is available."""
        return self.api_key is not None
    
    def _encode_image(self, image_path: str) -> tuple[str, str]:
        """Encode image to base64 with media type."""
        path = Path(image_path)
        
        # Determine media type
        suffix = path.suffix.lower()
        media_types = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".gif": "image/gif",
            ".webp": "image/webp",
        }
        media_type = media_types.get(suffix, "image/png")
        
        # Read and encode
        with open(path, "rb") as f:
            data = base64.standard_b64encode(f.read()).decode("utf-8")
        
        return data, media_type
    
    def _call_vision(self, image_path: str, prompt: str, system: str = "") -> Dict[str, Any]:
        """Call Claude Vision API."""
        start = time.time()
        
        image_data, media_type = self._encode_image(image_path)
        
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": image_data,
                        },
                    },
                    {
                        "type": "text",
                        "text": prompt,
                    },
                ],
            }
        ]
        
        try:
            response = self.client.messages.create(
                model=self.config.model,
                max_tokens=self.config.max_tokens,
                system=system if system else "You are a form analysis assistant. Be concise and precise.",
                messages=messages,
                temperature=self.config.temperature,
            )
            
            elapsed = time.time() - start
            text = response.content[0].text
            
            return {
                "success": True,
                "response": text,
                "elapsed": elapsed,
                "tokens": response.usage.input_tokens + response.usage.output_tokens,
            }
            
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "elapsed": time.time() - start,
            }
    
    # ─────────────────────────────────────────────────────────────────────
    # FORM ANALYSIS
    # ─────────────────────────────────────────────────────────────────────
    
    def analyze_field(self, screenshot_path: str, field_description: str = "") -> Dict[str, Any]:
        """
        Analyze a form field from screenshot.
        
        Returns:
            {
                "field_type": "dropdown|text|checkbox|...",
                "label": "Field label text",
                "options": ["option1", "option2", ...],  # for dropdowns
                "current_value": "...",
                "required": true/false,
                "confidence": 0.95
            }
        """
        prompt = f"""Analyze this form field screenshot.
{f'Focus on: {field_description}' if field_description else ''}

Return JSON only (no markdown, no explanation):
{{
    "field_type": "text|email|phone|dropdown|autocomplete|checkbox|radio|file|date|unknown",
    "label": "exact label text",
    "options": ["list", "of", "options"],  // for dropdown/radio, empty [] otherwise
    "current_value": "currently selected/entered value or empty string",
    "required": true or false,
    "placeholder": "placeholder text if any",
    "confidence": 0.0 to 1.0
}}"""

        result = self._call_vision(screenshot_path, prompt)
        
        if result["success"]:
            try:
                # Parse JSON from response
                text = result["response"]
                # Handle potential markdown code blocks
                if "```json" in text:
                    text = text.split("```json")[1].split("```")[0]
                elif "```" in text:
                    text = text.split("```")[1].split("```")[0]
                
                parsed = json.loads(text.strip())
                result["parsed"] = parsed
            except json.JSONDecodeError:
                result["parsed"] = None
        
        return result
    
    def analyze_form(self, screenshot_path: str) -> Dict[str, Any]:
        """
        Analyze entire form from screenshot.
        
        Returns list of all visible fields.
        """
        prompt = """Analyze this job application form screenshot.

List ALL visible form fields. Return JSON array only (no markdown):
[
    {
        "label": "field label",
        "field_type": "text|email|dropdown|checkbox|...",
        "required": true/false,
        "options": ["if", "dropdown"],
        "current_value": "if filled",
        "position": "top|middle|bottom of form"
    }
]

Include every visible field: name, email, phone, dropdowns, checkboxes, etc."""

        result = self._call_vision(screenshot_path, prompt)
        
        if result["success"]:
            try:
                text = result["response"]
                if "```json" in text:
                    text = text.split("```json")[1].split("```")[0]
                elif "```" in text:
                    text = text.split("```")[1].split("```")[0]
                
                parsed = json.loads(text.strip())
                result["fields"] = parsed
            except json.JSONDecodeError:
                result["fields"] = []
        
        return result
    
    def get_best_answer(
        self, 
        screenshot_path: str, 
        field_label: str,
        options: List[str],
        profile_context: str
    ) -> Dict[str, Any]:
        """
        Get best answer for a dropdown/select field.
        
        Args:
            screenshot_path: Screenshot of the field
            field_label: Field label text
            options: Available options
            profile_context: User profile summary
            
        Returns:
            {"answer": "selected option", "confidence": 0.95, "reason": "..."}
        """
        prompt = f"""Job application form field:
Label: {field_label}
Available options: {options}

Applicant profile:
{profile_context}

Which option should be selected? Return JSON only:
{{
    "answer": "exact option text to select",
    "confidence": 0.0 to 1.0,
    "reason": "brief explanation"
}}"""

        result = self._call_vision(screenshot_path, prompt)
        
        if result["success"]:
            try:
                text = result["response"]
                if "```" in text:
                    text = text.split("```")[1].split("```")[0]
                    if text.startswith("json"):
                        text = text[4:]
                
                parsed = json.loads(text.strip())
                result["parsed"] = parsed
                result["answer"] = parsed.get("answer")
            except:
                result["answer"] = None
        
        return result
    
    def verify_field_filled(
        self, 
        screenshot_path: str, 
        expected_value: str,
        field_label: str = ""
    ) -> Dict[str, Any]:
        """
        Verify that a field was filled correctly.
        
        Returns:
            {"verified": true/false, "actual_value": "...", "match": true/false}
        """
        prompt = f"""Check this form field:
{f'Label: {field_label}' if field_label else ''}
Expected value: {expected_value}

What value is currently shown in the field? Return JSON only:
{{
    "actual_value": "value currently in field",
    "matches_expected": true or false,
    "has_error": true or false,
    "error_message": "if any validation error is shown"
}}"""

        result = self._call_vision(screenshot_path, prompt)
        
        if result["success"]:
            try:
                text = result["response"]
                if "```" in text:
                    text = text.split("```")[1].split("```")[0]
                    if text.startswith("json"):
                        text = text[4:]
                
                parsed = json.loads(text.strip())
                result["verified"] = parsed.get("matches_expected", False)
                result["actual_value"] = parsed.get("actual_value", "")
            except:
                result["verified"] = False
        
        return result
    
    def find_field_selector(self, screenshot_path: str, field_label: str) -> Dict[str, Any]:
        """
        Help find CSS selector for a field based on screenshot analysis.
        
        Returns suggestions for locating the field programmatically.
        """
        prompt = f"""I need to automate filling the field "{field_label}" in this form.

Based on the screenshot, suggest how to locate this field:
1. What's the likely HTML element type? (input, select, div with role, etc.)
2. What CSS selectors might work?
3. What text/label is nearby that could help locate it?

Return JSON only:
{{
    "element_type": "input|select|div|button",
    "likely_id_pattern": "possible id like 'gender' or 'field-123'",
    "likely_selectors": ["#id", "[name='...']", "label text"],
    "nearby_text": "text that appears near this field",
    "suggestions": "how to interact with this field"
}}"""

        return self._call_vision(screenshot_path, prompt)
    
    def generate_custom_answer(
        self,
        question: str,
        profile_context: str,
        job_context: str = "",
        max_words: int = 100
    ) -> Dict[str, Any]:
        """
        Generate answer for custom text question (no screenshot needed).
        
        Args:
            question: The question text
            profile_context: User profile summary
            job_context: Job description context
            max_words: Maximum answer length
        """
        prompt = f"""Job application question: {question}

Applicant profile:
{profile_context}

{f'Job context: {job_context}' if job_context else ''}

Write a professional, concise answer (max {max_words} words).
Focus on relevant experience and achievements.
Be specific with numbers and examples where possible.

Return JSON only:
{{
    "answer": "your answer here",
    "word_count": N
}}"""

        try:
            response = self.client.messages.create(
                model=self.config.model,
                max_tokens=500,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
            )
            
            text = response.content[0].text
            
            try:
                if "```" in text:
                    text = text.split("```")[1].split("```")[0]
                    if text.startswith("json"):
                        text = text[4:]
                parsed = json.loads(text.strip())
                return {
                    "success": True,
                    "answer": parsed.get("answer", ""),
                }
            except:
                # If JSON parsing fails, use the raw text
                return {
                    "success": True,
                    "answer": text.strip(),
                }
                
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
            }


# ═══════════════════════════════════════════════════════════════════════════
# QUICK TEST
# ═══════════════════════════════════════════════════════════════════════════

def test_vision():
    """Quick test of Vision AI."""
    vision = VisionAI()
    
    if not vision.available:
        print("❌ Claude API key not configured")
        print("   Set ANTHROPIC_API_KEY environment variable")
        return
    
    print("✅ Vision AI available")
    print(f"   Model: {vision.config.model}")
    
    # Test text generation (no image)
    print("\n📝 Testing text generation...")
    result = vision.generate_custom_answer(
        question="Why do you want to work at this company?",
        profile_context="Senior TPM with 15 years experience in fintech",
        max_words=50
    )
    
    if result["success"]:
        print(f"   ✅ Answer: {result['answer'][:100]}...")
    else:
        print(f"   ❌ Error: {result.get('error')}")


if __name__ == "__main__":
    test_vision()
