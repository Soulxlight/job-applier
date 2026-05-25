import requests
from typing import List, Dict, Any
from .base import BaseScraper


class LeverScraper(BaseScraper):
    """
    Scrapes Lever job boards given a list of company slugs.
    Lever API: https://api.lever.co/v0/postings/{company}?mode=json
    """

    def search(self, keywords: str, location: str, remote_only: bool = False, max_results: int = 25) -> List[Dict]:
        jobs = []
        company_slugs = self.config.get('lever_companies', [])
        if not company_slugs:
            return jobs

        for slug in company_slugs:
            try:
                params = {'mode': 'json'}
                if remote_only:
                    params['workplaceType'] = 'remote'
                resp = requests.get(
                    f'https://api.lever.co/v0/postings/{slug}',
                    params=params,
                    timeout=15,
                )
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                print(f'[Lever] Failed for {slug}: {e}')
                continue

            for job in data[:max_results]:
                title = job.get('text', '')
                if keywords.lower() not in title.lower():
                    continue

                categories = job.get('categories', {})
                loc = categories.get('location', '')
                if remote_only and 'remote' not in loc.lower():
                    continue

                jobs.append(self._normalize_job({
                    'title': title,
                    'company': slug.replace('-', ' ').title(),
                    'location': loc,
                    'url': job.get('hostedUrl', ''),
                    'platform': 'lever',
                    'easy_apply': False,
                    'external_id': job.get('id', ''),
                    'description': job.get('descriptionPlain', ''),
                }))

        return jobs
