import time
from typing import Dict, Any
from playwright.sync_api import TimeoutError as PWTimeout
from .base import BaseApplier


class GenericApplier(BaseApplier):
    """
    Best-effort applier for Greenhouse, Lever, ZipRecruiter, and other ATS platforms.
    Opens the job URL, fills common fields, uploads resume, and tries to submit.
    """

    SUBMIT_SELECTORS = [
        'button[type="submit"]',
        'input[type="submit"]',
        'button:has-text("Submit")',
        'button:has-text("Apply")',
        'button:has-text("Send application")',
        '#submit_app',
        '.submit-btn',
    ]
    NEXT_SELECTORS = [
        'button:has-text("Next")',
        'button:has-text("Continue")',
        'button[type="button"]:has-text("Next")',
    ]

    def apply(self, job: Dict, cover_letter: str, resume_path: str) -> bool:
        page = self.ctx.new_page()
        try:
            page.goto(job['url'], timeout=20000)
            page.wait_for_load_state('networkidle', timeout=15000)

            # Greenhouse / Lever have an explicit "Apply" button first
            apply_btn = page.query_selector(
                'a:has-text("Apply for this job"), a:has-text("Apply Now"), '
                'button:has-text("Apply for this job"), .postings-btn, #apply-button'
            )
            if apply_btn:
                apply_btn.click()
                time.sleep(2)

            for step in range(8):
                time.sleep(1.5)
                self._fill_common_fields(page, self.config.get('profile', {}))
                self._upload_resume(page, resume_path)

                if cover_letter:
                    try:
                        for sel in ['textarea[name*="cover"]', 'textarea[id*="cover"]', '#cover_letter', 'textarea']:
                            el = page.query_selector(sel)
                            if el:
                                el.fill(cover_letter)
                                break
                    except Exception:
                        pass

                # Try to submit
                for sel in self.SUBMIT_SELECTORS:
                    btn = page.query_selector(sel)
                    if btn and btn.is_visible():
                        if not self.config.get('dry_run', False):
                            btn.click()
                            time.sleep(2)
                        return True

                # Try next step
                advanced = False
                for sel in self.NEXT_SELECTORS:
                    btn = page.query_selector(sel)
                    if btn and btn.is_visible():
                        btn.click()
                        advanced = True
                        break
                if not advanced:
                    break

            return False
        except PWTimeout:
            print(f'[Generic] Timeout applying to {job["url"]}')
            return False
        finally:
            try:
                page.close()
            except Exception:
                pass
