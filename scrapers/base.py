from abc import ABC, abstractmethod
from typing import List, Dict, Any


class BaseScraper(ABC):
    def __init__(self, config: Dict[str, Any]):
        self.config = config

    @abstractmethod
    def search(self, keywords: str, location: str, **kwargs) -> List[Dict]:
        """Return list of job dicts with keys: title, company, location, url, platform, etc."""
        pass

    def _normalize_job(self, raw: Dict) -> Dict:
        return {
            'title': raw.get('title', '').strip(),
            'company': raw.get('company', '').strip(),
            'location': raw.get('location', '').strip(),
            'salary': raw.get('salary', '').strip() if raw.get('salary') else None,
            'url': raw.get('url', '').strip(),
            'description': raw.get('description', '').strip() if raw.get('description') else None,
            'platform': raw.get('platform', self.__class__.__name__.replace('Scraper', '').lower()),
            'job_type': raw.get('job_type', '').strip() if raw.get('job_type') else None,
            'remote': raw.get('remote', False),
            'easy_apply': raw.get('easy_apply', False),
            'external_id': raw.get('external_id'),
        }
