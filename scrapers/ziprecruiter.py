import re
import requests
from bs4 import BeautifulSoup
from typing import List, Dict, Any
from .base import BaseScraper

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36',
    'Accept-Language': 'en-US,en;q=0.9',
}


class ZipRecruiterScraper(BaseScraper):
    BASE_URL = 'https://www.ziprecruiter.com/jobs-search'

    def search(self, keywords: str, location: str, remote_only: bool = False, max_results: int = 25) -> List[Dict]:
        jobs = []
        params = {'search': keywords, 'location': location}
        if remote_only:
            params['refine_by_location_type'] = 'only_remote'

        try:
            resp = requests.get(self.BASE_URL, params=params, headers=HEADERS, timeout=15)
            resp.raise_for_status()
        except Exception as e:
            print(f'[ZipRecruiter] Request failed: {e}')
            return jobs

        soup = BeautifulSoup(resp.text, 'html.parser')
        cards = soup.select('article.job_result, div[data-testid="job-card"]')

        for card in cards[:max_results]:
            try:
                title_el = card.select_one('h2.title a, a[data-testid="job-title"]')
                company_el = card.select_one('a.company_name, [data-testid="job-company"]')
                location_el = card.select_one('[data-testid="job-location"], .location')
                salary_el = card.select_one('[data-testid="job-compensation"], .compensation')

                if not title_el:
                    continue

                url = title_el.get('href', '')
                if not url.startswith('http'):
                    url = 'https://www.ziprecruiter.com' + url

                jobs.append(self._normalize_job({
                    'title': title_el.get_text(strip=True),
                    'company': company_el.get_text(strip=True) if company_el else '',
                    'location': location_el.get_text(strip=True) if location_el else '',
                    'salary': salary_el.get_text(strip=True) if salary_el else None,
                    'url': url,
                    'platform': 'ziprecruiter',
                    'easy_apply': True,
                }))
            except Exception:
                continue

        return jobs
