"""
Context Discovery Module for V5 Form Filler

This module discovers field labels by analyzing surrounding DOM elements,
especially useful for Shadow DOM forms where standard label[for] doesn't work.

Main approach:
1. Traverse UP from input element through parent containers
2. Look for text in labels, legends, spans, divs near the input
3. Return the most relevant text as the field's contextual label
"""

from typing import Optional, Dict, List, Tuple
from playwright.sync_api import Page


class ContextDiscovery:
    """Discovers field context by analyzing surrounding DOM elements."""
    
    # JavaScript function to inject into page
    DISCOVERY_SCRIPT = '''
    (inputId) => {
        function findInShadow(root, selector) {
            let el = root.querySelector(selector);
            if (el) return el;
            
            const shadows = root.querySelectorAll('*');
            for (const s of shadows) {
                if (s.shadowRoot) {
                    el = findInShadow(s.shadowRoot, selector);
                    if (el) return el;
                }
            }
            return null;
        }
        
        const input = findInShadow(document, '#' + inputId);
        if (!input) return {found: false, context: []};
        
        const context = [];
        let container = input;
        
        // Traverse up to find contextual text
        for (let i = 0; i < 10 && container; i++) {
            container = container.parentElement || 
                       (container.getRootNode && container.getRootNode().host);
            
            if (!container || !container.querySelectorAll) continue;
            
            // Look for label-like elements
            const textEls = container.querySelectorAll(
                'label, legend, p, span, h3, h4, div, .question, [class*="label"], [class*="question"]'
            );
            
            for (const el of textEls) {
                if (el.contains(input)) continue;
                
                const txt = el.textContent.trim();
                // Filter: not too short, not too long, not already found
                if (txt && txt.length > 3 && txt.length < 150) {
                    if (!context.some(c => c.includes(txt) || txt.includes(c))) {
                        context.push(txt);
                    }
                }
            }
            
            // Stop if we found enough context
            if (context.length >= 2) break;
        }
        
        return {
            found: true,
            context: context.slice(0, 3),  // Max 3 context items
            inputId: inputId
        };
    }
    '''
    
    # Script to discover all fields with context
    DISCOVER_ALL_SCRIPT = '''
    () => {
        const results = [];
        
        function findAllInputs(root, depth = 0) {
            const inputs = root.querySelectorAll('input, select, textarea');
            
            for (const input of inputs) {
                if (input.type === 'hidden') continue;
                if (!input.offsetParent && input.type !== 'file') continue;
                
                // Get context by traversing up
                const context = [];
                let container = input;
                
                for (let i = 0; i < 10 && container; i++) {
                    container = container.parentElement || 
                               (container.getRootNode && container.getRootNode().host);
                    
                    if (!container || !container.querySelectorAll) continue;
                    
                    const textEls = container.querySelectorAll(
                        'label, legend, p, span, h3, h4, div'
                    );
                    
                    for (const el of textEls) {
                        if (el.contains(input)) continue;
                        const txt = el.textContent.trim();
                        if (txt && txt.length > 3 && txt.length < 150) {
                            if (!context.some(c => c.includes(txt) || txt.includes(c))) {
                                context.push(txt);
                            }
                        }
                    }
                    
                    if (context.length >= 2) break;
                }
                
                results.push({
                    id: input.id,
                    name: input.name,
                    type: input.type || input.tagName.toLowerCase(),
                    value: input.value || '',
                    placeholder: input.placeholder || '',
                    required: input.required || input.getAttribute('aria-required') === 'true',
                    context: context.slice(0, 3)
                });
            }
            
            // Recurse into shadow roots
            const all = root.querySelectorAll('*');
            for (const el of all) {
                if (el.shadowRoot) findAllInputs(el.shadowRoot, depth + 1);
            }
        }
        
        findAllInputs(document);
        return results;
    }
    '''
    
    def __init__(self, page: Page):
        self.page = page
    
    def discover_field_context(self, input_id: str) -> Optional[str]:
        """
        Discover contextual label for a specific field by ID.
        
        Returns the most relevant context string (usually the question/label).
        """
        result = self.page.evaluate(self.DISCOVERY_SCRIPT, input_id)
        
        if not result.get('found'):
            return None
        
        context = result.get('context', [])
        if not context:
            return None
        
        # Return the first context item (usually the most relevant)
        return context[0]
    
    def discover_all_fields(self) -> List[Dict]:
        """
        Discover all form fields with their contextual labels.
        
        Returns list of dicts with: id, type, value, placeholder, required, context
        """
        fields = self.page.evaluate(self.DISCOVER_ALL_SCRIPT)
        
        # Deduplicate by ID
        seen_ids = set()
        unique_fields = []
        for f in fields:
            if f['id'] and f['id'] not in seen_ids:
                seen_ids.add(f['id'])
                unique_fields.append(f)
        
        return unique_fields
    
    def get_field_question(self, field: Dict) -> str:
        """
        Extract the most likely question/label from field context.
        
        Prioritizes:
        1. Context text ending with '?' (questions)
        2. Context text ending with '*' (required labels)
        3. First context item
        4. Placeholder
        5. Field ID
        """
        context = field.get('context', [])
        
        # Look for question (ends with ?)
        for ctx in context:
            if '?' in ctx:
                return ctx
        
        # Look for required label (ends with *)
        for ctx in context:
            if ctx.endswith('*'):
                return ctx
        
        # First context item
        if context:
            return context[0]
        
        # Fallback to placeholder or ID
        return field.get('placeholder') or field.get('id', 'unknown')


def fill_field_in_shadow(page: Page, field_id: str, value: str) -> bool:
    """
    Fill a field that may be in Shadow DOM.
    
    Returns True if successful.
    """
    result = page.evaluate('''(args) => {
        const {fieldId, value} = args;
        
        function findInShadow(root, selector) {
            let el = root.querySelector(selector);
            if (el) return el;
            
            const shadows = root.querySelectorAll('*');
            for (const s of shadows) {
                if (s.shadowRoot) {
                    el = findInShadow(s.shadowRoot, selector);
                    if (el) return el;
                }
            }
            return null;
        }
        
        const input = findInShadow(document, '#' + fieldId);
        if (!input) return {success: false, error: 'not found'};
        
        input.scrollIntoView({block: 'center'});
        input.focus();
        input.value = value;
        input.dispatchEvent(new Event('input', {bubbles: true}));
        input.dispatchEvent(new Event('change', {bubbles: true}));
        input.blur();
        
        return {success: true, value: input.value};
    }''', {'fieldId': field_id, 'value': value})
    
    return result.get('success', False)


def click_radio_in_shadow(page: Page, id_part: str) -> bool:
    """
    Click a radio button by partial ID match (for Shadow DOM).
    
    Returns True if successful.
    """
    result = page.evaluate('''(idPart) => {
        function findInShadow(root) {
            const radios = root.querySelectorAll('input[type="radio"]');
            for (const radio of radios) {
                if (radio.id && radio.id.includes(idPart)) {
                    radio.scrollIntoView({block: 'center'});
                    radio.click();
                    return {success: true, id: radio.id, checked: radio.checked};
                }
            }
            
            const all = root.querySelectorAll('*');
            for (const el of all) {
                if (el.shadowRoot) {
                    const result = findInShadow(el.shadowRoot);
                    if (result && result.success) return result;
                }
            }
            return {success: false};
        }
        
        return findInShadow(document);
    }''', id_part)
    
    return result.get('success', False)


def check_form_errors(page: Page) -> List[str]:
    """
    Check for visible error messages on the form.
    
    Returns list of error message strings.
    """
    errors = page.evaluate('''() => {
        const errors = [];
        
        function findErrors(root) {
            const errorSelectors = [
                '.error', '.error-message', '.field-error', '.validation-error',
                '[class*="error"]', '[class*="invalid"]',
                '.help-block.text-danger', '.invalid-feedback',
                '[role="alert"]'
            ];
            
            for (const selector of errorSelectors) {
                const els = root.querySelectorAll(selector);
                for (const el of els) {
                    const text = el.textContent.trim();
                    if (text && text.length > 2 && text.length < 200 && el.offsetParent) {
                        if (!errors.includes(text)) {
                            errors.push(text);
                        }
                    }
                }
            }
            
            const all = root.querySelectorAll('*');
            for (const el of all) {
                if (el.shadowRoot) findErrors(el.shadowRoot);
            }
        }
        
        findErrors(document);
        return errors;
    }''')
    
    return errors
