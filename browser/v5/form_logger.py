"""
Form Fill Logger for V5

Logs all form filling sessions with:
- URL and job details
- Fields discovered and filled
- Errors encountered
- Success/failure status
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass, asdict


@dataclass
class FieldLog:
    """Log entry for a single field."""
    field_id: str
    field_type: str
    question: str  # Discovered context/label
    value_filled: str
    source: str  # profile, answer_library, ai, user_input
    success: bool
    error: Optional[str] = None


@dataclass 
class FormFillLog:
    """Complete log for a form fill session."""
    timestamp: str
    url: str
    title: str
    job_id: Optional[str]
    company: Optional[str]
    status: str  # started, completed, error
    
    fields_total: int = 0
    fields_filled: int = 0
    fields_skipped: int = 0
    fields_error: int = 0
    
    field_logs: List[Dict] = None
    errors: List[str] = None
    
    duration_seconds: float = 0.0
    
    def __post_init__(self):
        if self.field_logs is None:
            self.field_logs = []
        if self.errors is None:
            self.errors = []


class FormLogger:
    """Manages form fill logging."""
    
    def __init__(self, log_dir: str = None):
        if log_dir is None:
            log_dir = Path(__file__).parent.parent.parent / "logs" / "form_fills"
        
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        
        self.current_log: Optional[FormFillLog] = None
        self.start_time: Optional[datetime] = None
    
    def start_session(self, url: str, title: str = "", job_id: str = None, company: str = None) -> FormFillLog:
        """Start a new form fill session."""
        self.start_time = datetime.now()
        
        self.current_log = FormFillLog(
            timestamp=self.start_time.isoformat(),
            url=url,
            title=title,
            job_id=job_id,
            company=company,
            status="started"
        )
        
        return self.current_log
    
    def log_field(self, field_id: str, field_type: str, question: str, 
                  value: str, source: str, success: bool, error: str = None):
        """Log a field fill attempt."""
        if self.current_log is None:
            return
        
        field_log = FieldLog(
            field_id=field_id,
            field_type=field_type,
            question=question[:100] if question else "",
            value_filled=value[:50] if value else "",
            source=source,
            success=success,
            error=error
        )
        
        self.current_log.field_logs.append(asdict(field_log))
        self.current_log.fields_total += 1
        
        if success:
            self.current_log.fields_filled += 1
        elif error:
            self.current_log.fields_error += 1
        else:
            self.current_log.fields_skipped += 1
    
    def log_error(self, error: str):
        """Log a general error."""
        if self.current_log:
            self.current_log.errors.append(error)
    
    def end_session(self, status: str = "completed") -> str:
        """
        End the session and save log.
        
        Returns the log file path.
        """
        if self.current_log is None:
            return None
        
        self.current_log.status = status
        
        if self.start_time:
            self.current_log.duration_seconds = (datetime.now() - self.start_time).total_seconds()
        
        # Generate filename
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # Extract company name from URL or title for filename
        company_slug = "unknown"
        if self.current_log.company:
            company_slug = self.current_log.company.lower().replace(" ", "_")[:20]
        elif self.current_log.url:
            # Try to extract domain
            from urllib.parse import urlparse
            domain = urlparse(self.current_log.url).netloc
            company_slug = domain.split(".")[0][:20]
        
        filename = f"{timestamp}_{company_slug}.json"
        filepath = self.log_dir / filename
        
        # Save
        with open(filepath, "w") as f:
            json.dump(asdict(self.current_log), f, indent=2)
        
        self.current_log = None
        self.start_time = None
        
        return str(filepath)
    
    def get_recent_logs(self, n: int = 10) -> List[Dict]:
        """Get the N most recent log files."""
        log_files = sorted(self.log_dir.glob("*.json"), reverse=True)[:n]
        
        logs = []
        for filepath in log_files:
            try:
                with open(filepath) as f:
                    log = json.load(f)
                    log['_filepath'] = str(filepath)
                    logs.append(log)
            except:
                pass
        
        return logs
    
    def get_log_summary(self) -> Dict:
        """Get summary statistics from all logs."""
        logs = self.get_recent_logs(100)
        
        total_forms = len(logs)
        completed = sum(1 for l in logs if l.get('status') == 'completed')
        total_fields = sum(l.get('fields_filled', 0) for l in logs)
        total_errors = sum(len(l.get('errors', [])) for l in logs)
        
        return {
            'total_forms': total_forms,
            'completed': completed,
            'success_rate': completed / total_forms if total_forms > 0 else 0,
            'total_fields_filled': total_fields,
            'total_errors': total_errors
        }
