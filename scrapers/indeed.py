import re
import time
import requests
from bs4 import BeautifulSoup
from typing import List, Dict, Any
from .base import BaseScraper

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36',
    'Accept-Language': 'en-US,en;q=0.9',
}


class IndeedScraper(BaseScraper):
    BASE_URL = 'https://www.indeed.com/jobs'

    def search(self, keywords: str, location: str, remote_only: bool = False, max_results: int = 25) -> List[Dict]:
        jobs = []
        params = {'q': keywords, 'l': location, 'limit': 25}
        if remote_only:
            params['remotejob'] = '032b3046-06a3-4876-8dfd-474eb5e7ed11'

        try:
            resp = requests.get(self.BASE_URL, params=params, headers=HEADERS, timeout=15)
            resp.raise_for_status()
        except Exception as e:
            print(f'[Indeed] Request failed: {e}')
            return jobs

        soup = BeautifulSoup(resp.text, 'html.parser')
        cards = soup.select('div.job_seen_beacon, div.slider_item')

        for card in cards[:max_results]:
            try:
                title_el = card.select_one('h2.jobTitle a, a.jcs-JobTitle')
                company_el = card.select_one('[data-testid="company-name"], .companyName')
                location_el = card.select_one('[data-testid="text-location"], .companyLocation')
                salary_el = card.select_one('[data-testid="attribute_snippet_testid"], .salary-snippet-container')

                if not title_el:
                    continue

                href = title_el.get('href', '')
                job_id_match = re.search(r'jk=([a-z0-9]+)', href)
                url = f'https://www.indeed.com{href}' if href.startswith('/') else href

                jobs.append(self._normalize_job({
                    'title': title_el.get_text(strip=True),
                    'company': company_el.get_text(strip=True) if company_el else '',
                    'location': location_el.get_text(strip=True) if location_el else '',
                    'salary': salary_el.get_text(strip=True) if salary_el else None,
                    'url': url,
                    'platform': 'indeed',
                    'easy_apply': True,  # Indeed Apply jobs
                    'external_id': job_id_match.group(1) if job_id_match else None,
                }))
            except Exception:
                continue

        return jobs
