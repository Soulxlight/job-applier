from .linkedin import LinkedInScraper
from .indeed import IndeedScraper
from .ziprecruiter import ZipRecruiterScraper
from .greenhouse import GreenhouseScraper
from .lever import LeverScraper

SCRAPERS = {
    'linkedin': LinkedInScraper,
    'indeed': IndeedScraper,
    'ziprecruiter': ZipRecruiterScraper,
    'greenhouse': GreenhouseScraper,
    'lever': LeverScraper,
}
