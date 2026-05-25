import os
import time
import re
from typing import List, Dict, Any
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
from .base import BaseScraper

_HEADLESS = os.environ.get('PLAYWRIGHT_HEADLESS', '').lower() != 'false' \
            and not os.environ.get('DISPLAY')


class LinkedInScraper(BaseScraper):
    BASE_URL = 'https://www.linkedin.com/jobs/search'

    def search(self, keywords: str, location: str, remote_only: bool = False, max_results: int = 25) -> List[Dict]:
        jobs = []
        with sync_playwright() as p:
            browser = p.chromium.launch_persistent_context(
                user_data_dir=self.config.get('browser_profile', '/tmp/li_profile'),
                headless=_HEADLESS,
                args=['--disable-blink-features=AutomationControlled',
                      '--no-sandbox', '--disable-dev-shm-usage'],
            )
            page = browser.new_page()
            try:
                params = f'?keywords={keywords.replace(" ", "%20")}&location={location.replace(" ", "%20")}'
                if remote_only:
                    params += '&f_WT=2'
                params += '&f_LF=f_AL'  # Easy Apply filter
                page.goto(f'{self.BASE_URL}{params}')
                page.wait_for_load_state('networkidle', timeout=15000)

                # Check if login required
                if 'login' in page.url or 'authwall' in page.url:
                    page.goto('https://www.linkedin.com/login')
                    print('[LinkedIn] Not logged in — please log in manually in the browser window, then press Enter.')
                    input()
                    page.goto(f'{self.BASE_URL}{params}')
                    page.wait_for_load_state('networkidle', timeout=15000)

                job_cards = page.query_selector_all('.job-card-container, .jobs-search-results__list-item')
                for card in job_cards[:max_results]:
                    try:
                        title_el = card.query_selector('.job-card-list__title, .job-card-container__link')
                        company_el = card.query_selector('.job-card-container__company-name, .artdeco-entity-lockup__subtitle')
                        location_el = card.query_selector('.job-card-container__metadata-item, .artdeco-entity-lockup__caption')
                        link_el = card.query_selector('a[href*="/jobs/view/"]')
                        easy_apply_el = card.query_selector('.job-card-container__apply-method')

                        if not (title_el and link_el):
                            continue

                        url = link_el.get_attribute('href') or ''
                        if '?' in url:
                            url = url.split('?')[0]
                        job_id_match = re.search(r'/jobs/view/(\d+)', url)

                        jobs.append(self._normalize_job({
                            'title': title_el.inner_text(),
                            'company': company_el.inner_text() if company_el else '',
                            'location': location_el.inner_text() if location_el else '',
                            'url': f'https://www.linkedin.com{url}' if url.startswith('/') else url,
                            'platform': 'linkedin',
                            'easy_apply': easy_apply_el is not None,
                            'external_id': job_id_match.group(1) if job_id_match else None,
                        }))
                    except Exception:
                        continue
            except PWTimeout:
                print('[LinkedIn] Page load timed out.')
            finally:
                browser.close()
        return jobs
