"""
Form Sandbox Server
Test forms for Smart Filler development

Usage:
    python sandbox/server.py
    # Open http://localhost:8888
"""

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
import uvicorn

app = FastAPI(title="Form Sandbox")

TEMPLATES_DIR = Path(__file__).parent / "templates"


@app.get("/", response_class=HTMLResponse)
async def index():
    """List all available test forms"""
    forms = []
    for f in TEMPLATES_DIR.glob("*.html"):
        forms.append(f.stem)
    
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Form Sandbox</title>
        <style>
            body { font-family: system-ui; max-width: 800px; margin: 50px auto; padding: 20px; }
            h1 { color: #333; }
            .form-list { list-style: none; padding: 0; }
            .form-list li { margin: 10px 0; }
            .form-list a { 
                display: block; padding: 15px 20px; 
                background: #f5f5f5; border-radius: 8px;
                text-decoration: none; color: #333;
                transition: background 0.2s;
            }
            .form-list a:hover { background: #e0e0e0; }
            .form-list .desc { font-size: 0.9em; color: #666; margin-top: 5px; }
        </style>
    </head>
    <body>
        <h1>🧪 Form Sandbox</h1>
        <p>Test forms for Smart Filler V4.0 development</p>
        <ul class="form-list">
    """
    
    descriptions = {
        "all_field_types": "All HTML input types in one form",
        "greenhouse": "Greenhouse-style application form",
        "lever": "Lever-style application form",
        "workday": "Workday-style multi-step form",
        "react_select": "React Select components",
        "radio_groups": "Radio button groups",
        "date_pickers": "Various date picker implementations",
        "edge_cases": "Problematic/tricky form patterns",
        "multi_step": "Multi-step wizard form",
    }
    
    for form in sorted(forms):
        desc = descriptions.get(form, "Test form")
        html += f'<li><a href="/{form}"><strong>{form}</strong><div class="desc">{desc}</div></a></li>'
    
    html += """
        </ul>
    </body>
    </html>
    """
    return html


@app.get("/{form_name}", response_class=HTMLResponse)
async def serve_form(form_name: str):
    """Serve a specific test form"""
    form_path = TEMPLATES_DIR / f"{form_name}.html"
    if form_path.exists():
        return form_path.read_text()
    return HTMLResponse(f"<h1>Form not found: {form_name}</h1>", status_code=404)


@app.post("/submit")
async def handle_submit(request: Request):
    """Handle form submission - just echo back the data"""
    form_data = await request.form()
    return {
        "status": "received",
        "fields": dict(form_data)
    }


if __name__ == "__main__":
    print("\n🧪 Form Sandbox Server")
    print("=" * 50)
    print(f"Templates: {TEMPLATES_DIR}")
    print("URL: http://localhost:8888")
    print("=" * 50 + "\n")
    uvicorn.run(app, host="0.0.0.0", port=8888)
