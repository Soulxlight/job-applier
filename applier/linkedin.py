import time
from typing import Dict, Any
from playwright.sync_api import TimeoutError as PWTimeout
from .base import BaseApplier


class LinkedInApplier(BaseApplier):
    def apply(self, job: Dict, cover_letter: str, resume_path: str) -> bool:
        page = self.ctx.new_page()
        try:
            page.goto(job['url'], timeout=20000)
            page.wait_for_load_state('networkidle', timeout=15000)

            # Click Easy Apply button
            apply_btn = page.query_selector('button.jobs-apply-button, button[aria-label*="Easy Apply"]')
            if not apply_btn:
                print(f'[LinkedIn] No Easy Apply button on {job["url"]}')
                return False

            apply_btn.click()
            page.wait_for_selector('.jobs-easy-apply-modal', timeout=10000)

            # Step through the multi-step form
            for step in range(10):
                time.sleep(1)

                # Fill common fields
                self._fill_common_fields(page, self.config.get('profile', {}))

                # Upload resume if there's a file input
                self._upload_resume(page, resume_path)

                # Fill cover letter textarea if present
                if cover_letter:
                    try:
                        cl_area = page.query_selector('textarea[id*="cover"], textarea[aria-label*="cover"]')
                        if cl_area:
                            cl_area.fill(cover_letter)
                    except Exception:
                        pass

                # Check for Next / Review / Submit button
                submit_btn = page.query_selector('button[aria-label="Submit application"]')
                if submit_btn:
                    if not self.config.get('dry_run', False):
                        submit_btn.click()
                        time.sleep(2)
                    return True

                next_btn = page.query_selector('button[aria-label="Continue to next step"], button[aria-label*="Review"]')
                if next_btn:
                    next_btn.click()
                else:
                    # Try generic primary button
                    primary = page.query_selector('.jobs-easy-apply-footer button.artdeco-button--primary')
                    if primary:
                        primary.click()
                    else:
                        break

            return False
        except PWTimeout:
            print(f'[LinkedIn] Timeout applying to {job["url"]}')
            return False
        finally:
            page.close()
