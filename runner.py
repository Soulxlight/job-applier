"""
Background engine: scrapes jobs and submits applications.
Called via Flask routes, runs in a thread.
"""
import os
import threading
from datetime import datetime
from playwright.sync_api import sync_playwright

# On headless servers (Render, etc.) there is no display; run Chromium headless.
# Set PLAYWRIGHT_HEADLESS=false locally if you want to watch the browser.
_HEADLESS = os.environ.get('PLAYWRIGHT_HEADLESS', '').lower() != 'false' \
            and not os.environ.get('DISPLAY')
from scrapers import SCRAPERS
from applier import APPLIERS
from cover_letter import render as render_cover_letter
from database import db, Job, Application, SearchConfig


_lock = threading.Lock()
_status = {'running': False, 'message': 'Idle', 'progress': 0, 'total': 0}


def get_status():
    return dict(_status)


def _update(message: str, progress: int = None, total: int = None):
    _status['message'] = message
    if progress is not None:
        _status['progress'] = progress
    if total is not None:
        _status['total'] = total


def scrape_jobs(app):
    """Discover new jobs and add them to the DB. Runs in background thread."""
    if _status['running']:
        return
    threading.Thread(target=_scrape_worker, args=(app,), daemon=True).start()


def _scrape_worker(app):
    with _lock:
        _status['running'] = True
        _status['message'] = 'Starting scrape...'
        _status['progress'] = 0

    try:
        with app.app_context():
            config = SearchConfig.get()
            platforms = [p.strip() for p in (config.platforms or '').split(',') if p.strip()]
            keywords = config.keywords or ''
            location = config.location or ''
            scraper_config = _build_scraper_config(app)
            new_count = 0

            for i, platform in enumerate(platforms):
                _update(f'Searching {platform}...', progress=i, total=len(platforms))
                cls = SCRAPERS.get(platform)
                if not cls:
                    continue
                try:
                    scraper = cls(scraper_config)
                    jobs = scraper.search(keywords, location, remote_only=config.remote_only)
                except Exception as e:
                    print(f'[Runner] Scraper error on {platform}: {e}')
                    continue

                blacklist = {c.strip().lower() for c in (config.blacklisted_companies or '').splitlines() if c.strip()}

                for job_data in jobs:
                    if job_data['company'].lower() in blacklist:
                        continue
                    if Job.query.filter_by(url=job_data['url']).first():
                        continue
                    job = Job(
                        status='approved' if config.auto_approve else 'pending',
                        **{k: v for k, v in job_data.items() if hasattr(Job, k)},
                    )
                    db.session.add(job)
                    new_count += 1

            db.session.commit()
            _update(f'Done — {new_count} new jobs found.', progress=len(platforms), total=len(platforms))
    except Exception as e:
        _update(f'Error: {e}')
    finally:
        _status['running'] = False


def apply_approved(app):
    """Apply to all approved jobs. Runs in background thread."""
    if _status['running']:
        return
    threading.Thread(target=_apply_worker, args=(app,), daemon=True).start()


def _apply_worker(app):
    with _lock:
        _status['running'] = True

    try:
        with app.app_context():
            config = SearchConfig.get()
            approved = Job.query.filter_by(status='approved').all()
            limit = config.max_applications_per_run or 10
            approved = approved[:limit]
            _update(f'Applying to {len(approved)} jobs...', progress=0, total=len(approved))

            if not approved:
                _update('No approved jobs to apply to.')
                return

            scraper_config = _build_scraper_config(app)
            profile = scraper_config.get('profile', {})
            cover_template = _load_cover_template(app)
            resume_path = scraper_config.get('resume_path', '')

            with sync_playwright() as p:
                browser = p.chromium.launch_persistent_context(
                    user_data_dir=scraper_config.get('browser_profile', '/tmp/job_browser'),
                    headless=_HEADLESS,
                    args=['--disable-blink-features=AutomationControlled',
                          '--no-sandbox', '--disable-dev-shm-usage'],
                )

                for i, job in enumerate(approved):
                    _update(f'Applying to {job.title} @ {job.company}...', progress=i + 1)
                    cover_letter = render_cover_letter(cover_template, job.__dict__, profile)
                    applier_cls = APPLIERS.get(job.platform, APPLIERS.get('greenhouse'))

                    try:
                        applier = applier_cls(scraper_config, browser)
                        success = applier.apply(job.__dict__, cover_letter, resume_path)
                    except Exception as e:
                        print(f'[Runner] Apply error: {e}')
                        success = False

                    job.status = 'applied' if success else 'failed'
                    job.applied_at = datetime.utcnow() if success else None

                    app_record = Application(
                        job_id=job.id,
                        cover_letter=cover_letter,
                        resume_path=resume_path,
                        status='submitted' if success else 'failed',
                    )
                    db.session.add(app_record)
                    db.session.commit()

                browser.close()

            _update(f'Finished. Applied to {sum(1 for j in approved if j.status == "applied")} jobs.')
    except Exception as e:
        _update(f'Error: {e}')
    finally:
        _status['running'] = False


def _build_scraper_config(app):
    import yaml, os
    config_path = os.path.join(app.root_path, 'config.yaml')
    if os.path.exists(config_path):
        with open(config_path) as f:
            return yaml.safe_load(f) or {}
    return {}


def _load_cover_template(app):
    import os
    path = os.path.join(app.root_path, 'cover_letter_template.txt')
    if os.path.exists(path):
        with open(path) as f:
            return f.read()
    from cover_letter import DEFAULT_TEMPLATE
    return DEFAULT_TEMPLATE
