"""
Tests for FormAnalyzer.

Tests the form analysis logic without requiring a real browser.
Uses mocked Playwright page objects.
"""

import sys
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
from dataclasses import asdict

sys.path.insert(0, str(Path(__file__).parent.parent))

from browser.form_analyzer import FormAnalyzer, FormField, FormSchema


# ============ Fixtures ============

@pytest.fixture
def mock_page():
    """Create a mock Playwright page."""
    page = MagicMock()

    # Mock event handlers
    page.on = MagicMock()

    return page


@pytest.fixture
def mock_element():
    """Create a mock form element."""
    element = MagicMock()
    element.get_attribute = MagicMock(return_value=None)
    element.evaluate = MagicMock(return_value="input")
    element.is_visible = MagicMock(return_value=True)
    element.inner_text = MagicMock(return_value="")
    return element


# ============ FormField Tests ============

class TestFormField:
    """Tests for FormField dataclass."""

    def test_default_values(self):
        """Should have sensible defaults."""
        field = FormField()

        assert field.selector == ""
        assert field.required is False
        assert field.is_visible is True
        assert field.confidence == 0.0
        assert field.options == []

    def test_field_with_values(self):
        """Should accept custom values."""
        field = FormField(
            selector="#email",
            element_id="email",
            name="email",
            html_type="input",
            input_type="email",
            label_text="Email Address",
            required=True,
        )

        assert field.selector == "#email"
        assert field.required is True
        assert field.input_type == "email"


# ============ FormSchema Tests ============

class TestFormSchema:
    """Tests for FormSchema dataclass."""

    def test_default_values(self):
        """Should have sensible defaults."""
        schema = FormSchema()

        assert schema.form_action == ""
        assert schema.form_method == ""
        assert schema.fields == []
        assert schema.api_schema == {}

    def test_schema_with_fields(self):
        """Should store fields correctly."""
        fields = [
            FormField(selector="#name", label_text="Name"),
            FormField(selector="#email", label_text="Email"),
        ]
        schema = FormSchema(
            form_action="/submit",
            form_method="POST",
            fields=fields,
        )

        assert len(schema.fields) == 2
        assert schema.form_method == "POST"


# ============ FormAnalyzer Tests ============

class TestFormAnalyzerInit:
    """Tests for FormAnalyzer initialization."""

    def test_init_sets_up_interception(self, mock_page):
        """Should set up network interception on init."""
        analyzer = FormAnalyzer(mock_page)

        # Should have called page.on for request and response
        assert mock_page.on.call_count == 2

        call_args = [call[0][0] for call in mock_page.on.call_args_list]
        assert "request" in call_args
        assert "response" in call_args

    def test_init_empty_intercepted_lists(self, mock_page):
        """Should start with empty intercepted lists."""
        analyzer = FormAnalyzer(mock_page)

        assert analyzer.intercepted_requests == []
        assert analyzer.intercepted_responses == []


class TestAnalyzeHtml:
    """Tests for HTML analysis."""

    def test_analyze_empty_page(self, mock_page):
        """Should handle page with no form elements."""
        mock_page.query_selector_all = MagicMock(return_value=[])

        analyzer = FormAnalyzer(mock_page)
        fields = analyzer.analyze_html()

        assert fields == []

    def test_analyze_text_input(self, mock_page, mock_element):
        """Should analyze text input correctly."""
        mock_element.get_attribute = MagicMock(side_effect=lambda attr: {
            "id": "first_name",
            "name": "first_name",
            "type": "text",
            "placeholder": "Enter your first name",
            "required": None,
            "aria-label": None,
        }.get(attr))
        mock_element.evaluate = MagicMock(return_value="input")

        mock_page.query_selector_all = MagicMock(return_value=[mock_element])
        mock_page.query_selector = MagicMock(return_value=None)  # No label

        analyzer = FormAnalyzer(mock_page)
        fields = analyzer.analyze_html()

        assert len(fields) == 1
        assert fields[0].element_id == "first_name"
        assert fields[0].input_type == "text"
        assert fields[0].placeholder == "Enter your first name"

    def test_analyze_skips_hidden_fields(self, mock_page, mock_element):
        """Should skip hidden input fields."""
        mock_element.get_attribute = MagicMock(side_effect=lambda attr: {
            "id": "csrf_token",
            "name": "csrf_token",
            "type": "hidden",
        }.get(attr))
        mock_element.evaluate = MagicMock(return_value="input")

        mock_page.query_selector_all = MagicMock(return_value=[mock_element])

        analyzer = FormAnalyzer(mock_page)
        fields = analyzer.analyze_html()

        assert len(fields) == 0

    def test_analyze_select_with_options(self, mock_page, mock_element):
        """Should extract options from select elements."""
        mock_element.get_attribute = MagicMock(side_effect=lambda attr: {
            "id": "country",
            "name": "country",
            "type": None,
        }.get(attr))
        mock_element.evaluate = MagicMock(side_effect=lambda script: {
            "el => el.tagName.toLowerCase()": "select",
            "el => Array.from(el.options).map(o => ({value: o.value, text: o.text}))": [
                {"value": "us", "text": "United States"},
                {"value": "ca", "text": "Canada"},
            ],
        }.get(script, "select"))

        mock_page.query_selector_all = MagicMock(return_value=[mock_element])
        mock_page.query_selector = MagicMock(return_value=None)

        analyzer = FormAnalyzer(mock_page)
        fields = analyzer.analyze_html()

        assert len(fields) == 1
        assert fields[0].html_type == "select"
        assert "United States" in fields[0].options
        assert "Canada" in fields[0].options

    def test_analyze_with_label(self, mock_page, mock_element):
        """Should find associated label."""
        mock_element.get_attribute = MagicMock(side_effect=lambda attr: {
            "id": "email",
            "name": "email",
            "type": "email",
        }.get(attr))
        mock_element.evaluate = MagicMock(return_value="input")

        # Mock label element
        mock_label = MagicMock()
        mock_label.inner_text = MagicMock(return_value="Email Address *")

        mock_page.query_selector_all = MagicMock(return_value=[mock_element])
        mock_page.query_selector = MagicMock(return_value=mock_label)

        analyzer = FormAnalyzer(mock_page)
        fields = analyzer.analyze_html()

        assert len(fields) == 1
        assert fields[0].label_text == "Email Address *"


class TestAnalyzeFormAction:
    """Tests for form action analysis."""

    def test_analyze_form_action(self, mock_page):
        """Should extract form action and method."""
        mock_form = MagicMock()
        mock_form.get_attribute = MagicMock(side_effect=lambda attr: {
            "action": "/api/submit",
            "method": "post",
            "enctype": "multipart/form-data",
        }.get(attr))

        mock_page.query_selector_all = MagicMock(return_value=[mock_form])

        analyzer = FormAnalyzer(mock_page)
        form_info = analyzer.analyze_form_action()

        assert form_info["action"] == "/api/submit"
        assert form_info["method"] == "POST"
        assert form_info["enctype"] == "multipart/form-data"

    def test_analyze_form_action_defaults(self, mock_page):
        """Should use defaults for missing attributes."""
        mock_page.query_selector_all = MagicMock(return_value=[])

        analyzer = FormAnalyzer(mock_page)
        form_info = analyzer.analyze_form_action()

        assert form_info["method"] == "POST"


class TestParsePostData:
    """Tests for POST data parsing."""

    def test_parse_json_data(self, mock_page):
        """Should parse JSON POST data."""
        analyzer = FormAnalyzer(mock_page)

        data = '{"name": "John", "email": "john@example.com"}'
        result = analyzer._parse_post_data(data)

        assert result["name"] == "John"
        assert result["email"] == "john@example.com"

    def test_parse_form_urlencoded(self, mock_page):
        """Should parse form-urlencoded data."""
        analyzer = FormAnalyzer(mock_page)

        data = "name=John&email=john%40example.com"
        result = analyzer._parse_post_data(data)

        assert result["name"] == "John"
        assert result["email"] == "john@example.com"

    def test_parse_empty_data(self, mock_page):
        """Should handle empty data."""
        analyzer = FormAnalyzer(mock_page)

        result = analyzer._parse_post_data("")
        assert result == {}

        result = analyzer._parse_post_data(None)
        assert result == {}


class TestNetworkInterception:
    """Tests for network interception logic."""

    def test_intercept_post_request(self, mock_page):
        """Should capture POST requests."""
        analyzer = FormAnalyzer(mock_page)

        # Simulate a POST request
        mock_request = MagicMock()
        mock_request.method = "POST"
        mock_request.url = "https://example.com/api/submit"
        mock_request.post_data = '{"name": "Test"}'
        mock_request.headers = {"Content-Type": "application/json"}

        # Get the request handler that was registered
        request_handler = None
        for call in mock_page.on.call_args_list:
            if call[0][0] == "request":
                request_handler = call[0][1]
                break

        # Call the handler
        request_handler(mock_request)

        assert len(analyzer.intercepted_requests) == 1
        assert analyzer.intercepted_requests[0]["url"] == "https://example.com/api/submit"
        assert analyzer.intercepted_requests[0]["method"] == "POST"

    def test_ignore_get_request(self, mock_page):
        """Should ignore GET requests."""
        analyzer = FormAnalyzer(mock_page)

        mock_request = MagicMock()
        mock_request.method = "GET"
        mock_request.url = "https://example.com/api/data"

        # Get the request handler
        request_handler = None
        for call in mock_page.on.call_args_list:
            if call[0][0] == "request":
                request_handler = call[0][1]
                break

        request_handler(mock_request)

        assert len(analyzer.intercepted_requests) == 0


class TestFullAnalysis:
    """Tests for full form analysis."""

    def test_full_analysis_combines_sources(self, mock_page, mock_element):
        """Should combine HTML and network analysis."""
        # Setup mock elements
        mock_element.get_attribute = MagicMock(side_effect=lambda attr: {
            "id": "email",
            "name": "email",
            "type": "email",
        }.get(attr))
        mock_element.evaluate = MagicMock(return_value="input")

        mock_page.query_selector_all = MagicMock(return_value=[mock_element])
        mock_page.query_selector = MagicMock(return_value=None)
        mock_page.content = MagicMock(return_value="<html></html>")

        analyzer = FormAnalyzer(mock_page)
        schema = analyzer.full_analysis()

        assert isinstance(schema, FormSchema)
        assert len(schema.fields) == 1

    def test_full_analysis_calculates_confidence(self, mock_page, mock_element):
        """Should calculate confidence for fields."""
        mock_element.get_attribute = MagicMock(side_effect=lambda attr: {
            "id": "name",
            "name": "name",
            "type": "text",
        }.get(attr))
        mock_element.evaluate = MagicMock(return_value="input")

        mock_label = MagicMock()
        mock_label.inner_text = MagicMock(return_value="Full Name")

        mock_page.query_selector_all = MagicMock(return_value=[mock_element])
        mock_page.query_selector = MagicMock(return_value=mock_label)
        mock_page.content = MagicMock(return_value="<html></html>")

        analyzer = FormAnalyzer(mock_page)
        schema = analyzer.full_analysis()

        # Field with id, name, and label should have high confidence
        assert schema.fields[0].confidence > 0.7
