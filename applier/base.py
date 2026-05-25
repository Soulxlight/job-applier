from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
from pathlib import Path


class BaseApplier(ABC):
    def __init__(self, config: Dict[str, Any], browser_context):
        self.config = config
        self.ctx = browser_context

    @abstractmethod
    def apply(self, job: Dict, cover_letter: str, resume_path: str) -> bool:
        """Fill out and submit the application. Returns True on success."""
        pass

    def _fill_common_fields(self, page, profile: Dict):
        """Fill common application fields using profile data."""
        field_map = {
            'input[name*="first"], input[id*="first"]': profile.get('first_name', ''),
            'input[name*="last"], input[id*="last"]': profile.get('last_name', ''),
            'input[name*="email"], input[type="email"]': profile.get('email', ''),
            'input[name*="phone"], input[type="tel"]': profile.get('phone', ''),
            'input[name*="linkedin"], input[placeholder*="LinkedIn"]': profile.get('linkedin', ''),
            'input[name*="website"], input[name*="portfolio"]': profile.get('website', ''),
        }
        for selector, value in field_map.items():
            if not value:
                continue
            try:
                el = page.query_selector(selector)
                if el:
                    el.fill(value)
            except Exception:
                pass

    def _upload_resume(self, page, resume_path: str):
        try:
            file_input = page.query_selector('input[type="file"]')
            if file_input and resume_path and Path(resume_path).exists():
                file_input.set_input_files(resume_path)
        except Exception:
            pass

    def _screenshot(self, page, job_id: int, label: str = 'applied') -> Optional[str]:
        try:
            path = f'/tmp/job_{job_id}_{label}.png'
            page.screenshot(path=path)
            return path
        except Exception:
            return None
