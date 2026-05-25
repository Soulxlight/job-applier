import requests
from bs4 import BeautifulSoup
from typing import List, Dict, Any
from .base import BaseScraper


class GreenhouseScraper(BaseScraper):
    """
    Scrapes Greenhouse job boards given a list of company board tokens.
    Greenhouse board URLs follow: https://boards.greenhouse.io/{company_token}/jobs
    """

    def search(self, keywords: str, location: str, remote_only: bool = False, max_results: int = 25) -> List[Dict]:
        jobs = []
        company_tokens = self.config.get('greenhouse_companies', [])
        if not company_tokens:
            return jobs

        for token in company_tokens:
            try:
                resp = requests.get(
                    f'https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true',
                    timeout=15,
                )
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                print(f'[Greenhouse] Failed for {token}: {e}')
                continue

            for job in data.get('jobs', [])[:max_results]:
                title = job.get('title', '')
                if keywords.lower() not in title.lower():
                    continue

                loc = job.get('location', {}).get('name', '')
                if remote_only and 'remote' not in loc.lower():
                    continue

                jobs.append(self._normalize_job({
                    'title': title,
                    'company': token.replace('-', ' ').title(),
                    'location': loc,
                    'url': job.get('absolute_url', ''),
                    'platform': 'greenhouse',
                    'easy_apply': False,
                    'external_id': str(job.get('id', '')),
                    'description': BeautifulSoup(job.get('content', ''), 'html.parser').get_text('\n') if job.get('content') else None,
                }))

        return jobs
