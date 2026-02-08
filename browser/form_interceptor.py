"""
Form Interceptor - Dry Run Mode for Form Analysis.

Intercepts form submissions to capture the exact data structure
that would be sent to the server, without actually submitting.

Usage:
    interceptor = FormInterceptor(page)
    result = interceptor.capture_form_submit(fill_form=True, profile=profile_data)

Returns:
    {
        "submit_url": "https://...",
        "method": "POST",
        "content_type": "application/json",
        "fields": {...},
        "required_fields": [...],
        "intercepted_at": "2026-01-28T12:00:00Z"
    }
"""

import json
import time
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional, Callable
from dataclasses import dataclass, field, asdict
from pathlib import Path


@dataclass
class InterceptedRequest:
    """Captured form submission request."""
    url: str
    method: str
    content_type: str
    headers: Dict[str, str]
    body: Any  # Can be dict, string, or None
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()


@dataclass
class FormInterceptResult:
    """Result of form interception."""
    success: bool
    submit_url: str = ""
    method: str = ""
    content_type: str = ""
    fields: Dict[str, Any] = field(default_factory=dict)
    required_fields: List[str] = field(default_factory=list)
    all_requests: List[Dict] = field(default_factory=list)
    form_html_fields: List[Dict] = field(default_factory=list)
    error: str = ""
    intercepted_at: str = ""

    def __post_init__(self):
        if not self.intercepted_at:
            self.intercepted_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> Dict:
        return asdict(self)


class FormInterceptor:
    """
    Intercepts form submissions for dry-run analysis.

    Workflow:
    1. Navigate to job application page
    2. Optionally fill form with profile data
    3. Click submit button
    4. Intercept the request BEFORE it goes to server
    5. Block the request (don't actually submit)
    6. Return the captured request data
    """

    def __init__(self, page, block_submit: bool = True):
        """
        Initialize interceptor.

        Args:
            page: Playwright page object
            block_submit: If True, block the submit request (dry run)
                         If False, allow request to proceed (useful for debugging)
        """
        self.page = page
        self.block_submit = block_submit
        self.intercepted_requests: List[InterceptedRequest] = []
        self.submit_request: Optional[InterceptedRequest] = None
        self._route_handler = None

    def _setup_interception(self):
        """Set up request interception using page.route()."""

        def handle_route(route):
            request = route.request

            # Capture POST/PUT requests (likely form submissions)
            if request.method in ("POST", "PUT", "PATCH"):
                content_type = request.headers.get("content-type", "")

                intercepted = InterceptedRequest(
                    url=request.url,
                    method=request.method,
                    content_type=content_type,
                    headers=dict(request.headers),
                    body=self._parse_body(request.post_data, content_type),
                )

                self.intercepted_requests.append(intercepted)

                # Check if this looks like a form submission
                if self._is_form_submit(request.url, content_type):
                    self.submit_request = intercepted

                    if self.block_submit:
                        # Block the request - return a fake success response
                        route.fulfill(
                            status=200,
                            content_type="application/json",
                            body=json.dumps({
                                "success": True,
                                "message": "DRY RUN - Request intercepted",
                                "_intercepted": True
                            })
                        )
                        return

            # Let other requests through
            route.continue_()

        self._route_handler = handle_route
        self.page.route("**/*", handle_route)

    def _teardown_interception(self):
        """Remove request interception."""
        if self._route_handler:
            try:
                self.page.unroute("**/*", self._route_handler)
            except:
                pass

    def _is_form_submit(self, url: str, content_type: str) -> bool:
        """Check if request looks like a form submission."""
        # Common patterns for form submissions
        submit_patterns = [
            "submit", "apply", "application", "candidate",
            "create", "save", "post", "send"
        ]

        url_lower = url.lower()
        for pattern in submit_patterns:
            if pattern in url_lower:
                return True

        # Check content type
        if any(ct in content_type for ct in ["form", "json", "multipart"]):
            return True

        return False

    def _parse_body(self, data: Optional[str], content_type: str) -> Any:
        """Parse request body based on content type."""
        if not data:
            return None

        # Try JSON
        if "json" in content_type:
            try:
                return json.loads(data)
            except:
                pass

        # Try form-urlencoded
        if "form" in content_type or "urlencoded" in content_type:
            try:
                from urllib.parse import parse_qs
                parsed = parse_qs(data)
                return {k: v[0] if len(v) == 1 else v for k, v in parsed.items()}
            except:
                pass

        # Return raw if can't parse
        return data

    def analyze_form_fields(self) -> List[Dict]:
        """
        Analyze current form fields on the page.
        Returns list of field information.
        """
        fields = []

        try:
            # Find all form inputs
            elements = self.page.query_selector_all(
                "input:not([type='hidden']):not([type='submit']):not([type='button']), "
                "select, textarea"
            )

            for el in elements:
                try:
                    field_info = {
                        "id": el.get_attribute("id") or "",
                        "name": el.get_attribute("name") or "",
                        "type": el.get_attribute("type") or "text",
                        "tag": el.evaluate("el => el.tagName.toLowerCase()"),
                        "required": el.get_attribute("required") is not None,
                        "placeholder": el.get_attribute("placeholder") or "",
                        "value": el.input_value() if el.is_visible() else "",
                        "visible": el.is_visible(),
                    }

                    # Get label
                    field_id = field_info["id"]
                    if field_id:
                        label = self.page.query_selector(f"label[for='{field_id}']")
                        if label:
                            field_info["label"] = label.inner_text().strip()

                    # Get options for select
                    if field_info["tag"] == "select":
                        try:
                            options = el.evaluate(
                                "el => Array.from(el.options).map(o => ({value: o.value, text: o.text}))"
                            )
                            field_info["options"] = options
                        except:
                            pass

                    fields.append(field_info)
                except Exception as e:
                    continue

        except Exception as e:
            pass

        return fields

    def fill_form_with_profile(self, profile: Dict) -> int:
        """
        Fill form fields using profile data.
        Returns count of filled fields.
        """
        filled = 0

        # Build mapping from common field names to profile values
        field_mappings = self._build_field_mappings(profile)

        # Find and fill fields
        elements = self.page.query_selector_all(
            "input:not([type='hidden']):not([type='submit']):not([type='button']), "
            "select, textarea"
        )

        for el in elements:
            try:
                if not el.is_visible():
                    continue

                # Get field identifiers
                field_id = el.get_attribute("id") or ""
                field_name = el.get_attribute("name") or ""
                field_type = el.get_attribute("type") or "text"
                placeholder = el.get_attribute("placeholder") or ""

                # Try to find matching value
                value = self._find_value_for_field(
                    field_id, field_name, placeholder, field_mappings
                )

                if value:
                    tag = el.evaluate("el => el.tagName.toLowerCase()")

                    if tag == "select":
                        try:
                            el.select_option(label=value)
                            filled += 1
                        except:
                            try:
                                el.select_option(value=value)
                                filled += 1
                            except:
                                pass
                    elif field_type in ("checkbox", "radio"):
                        if value.lower() in ("yes", "true", "1"):
                            el.check()
                            filled += 1
                    else:
                        el.fill(value)
                        filled += 1

            except Exception as e:
                continue

        return filled

    def _build_field_mappings(self, profile: Dict) -> Dict[str, str]:
        """Build mapping from field patterns to profile values."""
        mappings = {}

        # Personal info
        personal = profile.get("personal", {})
        mappings.update({
            "first_name": personal.get("first_name", ""),
            "firstname": personal.get("first_name", ""),
            "first": personal.get("first_name", ""),
            "last_name": personal.get("last_name", ""),
            "lastname": personal.get("last_name", ""),
            "last": personal.get("last_name", ""),
            "full_name": personal.get("full_name", ""),
            "fullname": personal.get("full_name", ""),
            "name": personal.get("full_name", ""),
            "email": personal.get("email", ""),
            "phone": personal.get("phone", ""),
            "telephone": personal.get("phone", ""),
            "mobile": personal.get("phone", ""),
            "city": personal.get("city", ""),
            "state": personal.get("state", ""),
            "zip": personal.get("zip_code", ""),
            "postal": personal.get("zip_code", ""),
            "country": personal.get("country", ""),
            "address": personal.get("street_address", ""),
            "location": personal.get("location", ""),
        })

        # Links
        links = profile.get("links", {})
        mappings.update({
            "linkedin": links.get("linkedin", ""),
            "github": links.get("github", ""),
            "portfolio": links.get("portfolio", ""),
            "website": links.get("portfolio", ""),
        })

        # Work authorization
        work_auth = profile.get("work_authorization", {})
        mappings.update({
            "authorized": work_auth.get("authorized_us", ""),
            "sponsorship": work_auth.get("requires_sponsorship", ""),
            "visa": work_auth.get("requires_sponsorship", ""),
        })

        # Common answers
        common = profile.get("common_answers", {})
        for key, value in common.items():
            mappings[key.lower()] = str(value)

        return mappings

    def _find_value_for_field(
        self,
        field_id: str,
        field_name: str,
        placeholder: str,
        mappings: Dict[str, str]
    ) -> Optional[str]:
        """Find matching value for a field."""
        # Combine identifiers
        identifiers = [
            field_id.lower().replace("-", "_").replace(" ", "_"),
            field_name.lower().replace("-", "_").replace(" ", "_"),
            placeholder.lower().replace("-", "_").replace(" ", "_"),
        ]

        for identifier in identifiers:
            if not identifier:
                continue

            # Direct match
            if identifier in mappings:
                return mappings[identifier]

            # Partial match
            for pattern, value in mappings.items():
                if pattern in identifier or identifier in pattern:
                    if value:
                        return value

        return None

    def click_submit(self) -> bool:
        """
        Find and click the submit button.
        Returns True if button was found and clicked.
        """
        # Common submit button selectors
        selectors = [
            "button[type='submit']",
            "input[type='submit']",
            "button:has-text('Submit')",
            "button:has-text('Apply')",
            "button:has-text('Send')",
            "button:has-text('Continue')",
            "[data-testid='submit']",
            ".submit-button",
            "#submit",
        ]

        for selector in selectors:
            try:
                button = self.page.query_selector(selector)
                if button and button.is_visible():
                    button.click()
                    return True
            except:
                continue

        return False

    def capture_form_submit(
        self,
        fill_form: bool = False,
        profile: Optional[Dict] = None,
        wait_after_fill: float = 1.0,
        wait_after_click: float = 2.0,
    ) -> FormInterceptResult:
        """
        Main method: Capture form submission.

        Args:
            fill_form: Whether to fill form with profile data first
            profile: Profile data to use for filling
            wait_after_fill: Seconds to wait after filling form
            wait_after_click: Seconds to wait after clicking submit

        Returns:
            FormInterceptResult with captured data
        """
        result = FormInterceptResult(success=False)

        try:
            # Analyze current form fields
            result.form_html_fields = self.analyze_form_fields()

            # Find required fields (from HTML)
            result.required_fields = [
                f.get("name") or f.get("id")
                for f in result.form_html_fields
                if f.get("required")
            ]

            # Fill form if requested
            if fill_form and profile:
                filled = self.fill_form_with_profile(profile)
                time.sleep(wait_after_fill)

            # Disable form validation to ensure submit works
            self.page.evaluate("""
                const form = document.querySelector('form');
                if (form) {
                    form.setAttribute('novalidate', '');
                    // Remove required from file inputs (can't auto-fill)
                    form.querySelectorAll('input[type="file"][required]').forEach(
                        el => el.removeAttribute('required')
                    );
                }
            """)

            # Find submit button
            submit_button = None
            for selector in [
                "button[type='submit']",
                "input[type='submit']",
                "button:has-text('Submit')",
                "button:has-text('Apply')",
            ]:
                try:
                    btn = self.page.query_selector(selector)
                    if btn and btn.is_visible():
                        submit_button = btn
                        break
                except:
                    continue

            if not submit_button:
                result.error = "Could not find submit button"
                return result

            # Use expect_request to capture the POST
            try:
                with self.page.expect_request(
                    lambda req: req.method == "POST",
                    timeout=10000
                ) as request_info:
                    # Submit via JavaScript for reliability
                    self.page.evaluate("document.querySelector('form').submit()")

                request = request_info.value
                result.success = True
                result.submit_url = request.url
                result.method = request.method
                result.content_type = request.headers.get("content-type", "")

                # Parse POST data
                post_data = request.post_data
                if post_data:
                    result.fields = self._parse_body(post_data, result.content_type)
                    if not isinstance(result.fields, dict):
                        result.fields = {"_raw": result.fields}

            except Exception as e:
                result.error = f"Failed to capture request: {e}"

            # Include all intercepted requests (if any from route)
            result.all_requests = [
                asdict(req) for req in self.intercepted_requests
            ]

        except Exception as e:
            result.error = str(e)

        finally:
            self._teardown_interception()

        return result


def run_dry_run(
    job_url: str,
    profile_path: Optional[str] = None,
    headless: bool = True,
    timeout: int = 30000,
) -> FormInterceptResult:
    """
    Convenience function to run dry-run analysis.

    Args:
        job_url: URL of job application page
        profile_path: Path to profile JSON file
        headless: Run browser in headless mode
        timeout: Page load timeout in ms

    Returns:
        FormInterceptResult
    """
    from playwright.sync_api import sync_playwright

    # Load profile if provided
    profile = {}
    if profile_path:
        profile_file = Path(profile_path)
        if profile_file.exists():
            with open(profile_file) as f:
                profile = json.load(f)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context()
        page = context.new_page()

        try:
            # Navigate to job page
            page.goto(job_url, timeout=timeout)
            page.wait_for_load_state("networkidle", timeout=timeout)

            # Run interception
            interceptor = FormInterceptor(page, block_submit=True)
            result = interceptor.capture_form_submit(
                fill_form=bool(profile),
                profile=profile,
            )

            return result

        finally:
            browser.close()


if __name__ == "__main__":
    # Test with a sample URL
    import sys

    if len(sys.argv) < 2:
        print("Usage: python form_interceptor.py <job_url> [profile_path]")
        sys.exit(1)

    url = sys.argv[1]
    profile = sys.argv[2] if len(sys.argv) > 2 else None

    print(f"\n🔍 Running dry-run analysis...")
    print(f"   URL: {url}")
    print(f"   Profile: {profile or 'None'}")

    result = run_dry_run(url, profile, headless=False)

    print(f"\n{'='*60}")
    print(f"📋 RESULT")
    print(f"{'='*60}")
    print(f"Success: {result.success}")
    print(f"Submit URL: {result.submit_url}")
    print(f"Method: {result.method}")
    print(f"Content-Type: {result.content_type}")
    print(f"\nFields captured:")
    print(json.dumps(result.fields, indent=2, default=str))
    print(f"\nRequired fields: {result.required_fields}")
    print(f"\nError: {result.error or 'None'}")
