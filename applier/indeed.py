import time
from typing import Dict, Any
from playwright.sync_api import TimeoutError as PWTimeout
from .base import BaseApplier


class IndeedApplier(BaseApplier):
    def apply(self, job: Dict, cover_letter: str, resume_path: str) -> bool:
        page = self.ctx.new_page()
        try:
            page.goto(job['url'], timeout=20000)
            page.wait_for_load_state('networkidle', timeout=15000)

            apply_btn = page.query_selector('#indeedApplyButton, button[id*="apply"], a[id*="apply"]')
            if not apply_btn:
                # Try the generic apply area
                apply_btn = page.query_selector('[data-testid="apply-button"]')
            if not apply_btn:
                print(f'[Indeed] No apply button found at {job["url"]}')
                return False

            apply_btn.click()
            time.sleep(2)

            # Indeed may open in an iframe or new tab
            if len(page.context.pages) > 1:
                page = page.context.pages[-1]

            for step in range(8):
                time.sleep(1.5)
                self._fill_common_fields(page, self.config.get('profile', {}))
                self._upload_resume(page, resume_path)

                if cover_letter:
                    try:
                        cl_area = page.query_selector('textarea[name*="cover"], textarea[aria-label*="cover"]')
                        if cl_area:
                            cl_area.fill(cover_letter)
                    except Exception:
                        pass

                submit_btn = page.query_selector('button[type="submit"][data-testid*="submit"], button:has-text("Submit")')
                if submit_btn:
                    if not self.config.get('dry_run', False):
                        submit_btn.click()
                        time.sleep(2)
                    return True

                next_btn = page.query_selector('button:has-text("Continue"), button:has-text("Next")')
                if next_btn:
                    next_btn.click()
                else:
                    break

            return False
        except PWTimeout:
            print(f'[Indeed] Timeout applying to {job["url"]}')
            return False
        finally:
            try:
                page.close()
            except Exception:
                pass
