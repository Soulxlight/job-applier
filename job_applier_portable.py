#!/usr/bin/env python3
"""
Job Applier — portable single-file edition.
Run: python job_applier_portable.py
First run installs dependencies automatically.
"""
import subprocess, sys, importlib.util, os, tempfile, platform

# ── Auto-install deps ─────────────────────────────────────────────────────────
_PKGS = {
    'flask': 'flask',
    'flask-sqlalchemy': 'flask_sqlalchemy',
    'playwright': 'playwright',
    'beautifulsoup4': 'bs4',
    'requests': 'requests',
    'pyyaml': 'yaml',
    'Werkzeug': 'werkzeug',
}
_missing = [pkg for pkg, mod in _PKGS.items() if not importlib.util.find_spec(mod)]
if _missing:
    print(f'Installing: {", ".join(_missing)} ...')
    subprocess.check_call([sys.executable, '-m', 'pip', 'install', '-q'] + _missing)
    print('Done. Re-launching...')
    # os.execv is unreliable on Windows; spawn a new process and exit instead
    subprocess.Popen([sys.executable] + sys.argv)
    sys.exit(0)

# ── Imports ───────────────────────────────────────────────────────────────────
import re, threading, time, yaml
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional

import requests as http_requests
from bs4 import BeautifulSoup
from flask import (Flask, render_template_string, request, redirect,
                   url_for, jsonify, flash, send_from_directory)
from flask_sqlalchemy import SQLAlchemy
from jinja2 import DictLoader
from werkzeug.utils import secure_filename

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE_DIR       = Path(__file__).parent.resolve()
UPLOAD_DIR     = BASE_DIR / 'uploads'
DB_PATH        = BASE_DIR / 'jobs.db'
CONFIG_PATH    = BASE_DIR / 'config.yaml'
CL_PATH        = BASE_DIR / 'cover_letter_template.txt'
UPLOAD_DIR.mkdir(exist_ok=True)

# Cross-platform default browser profile location
_DEFAULT_BROWSER_PROFILE = str(Path(tempfile.gettempdir()) / 'job_applier_browser')

# ══════════════════════════════════════════════════════════════════════════════
# DATABASE
# ══════════════════════════════════════════════════════════════════════════════
db = SQLAlchemy()

class Job(db.Model):
    id          = db.Column(db.Integer, primary_key=True)
    title       = db.Column(db.String(200), nullable=False)
    company     = db.Column(db.String(200), nullable=False)
    location    = db.Column(db.String(200))
    salary      = db.Column(db.String(100))
    url         = db.Column(db.String(500), unique=True, nullable=False)
    description = db.Column(db.Text)
    platform    = db.Column(db.String(50))
    job_type    = db.Column(db.String(50))
    remote      = db.Column(db.Boolean, default=False)
    easy_apply  = db.Column(db.Boolean, default=False)
    status      = db.Column(db.String(30), default='pending')
    found_at    = db.Column(db.DateTime, default=datetime.utcnow)
    applied_at  = db.Column(db.DateTime)
    notes       = db.Column(db.Text)
    external_id = db.Column(db.String(200))

class Application(db.Model):
    id              = db.Column(db.Integer, primary_key=True)
    job_id          = db.Column(db.Integer, db.ForeignKey('job.id'), nullable=False)
    job             = db.relationship('Job', backref='application')
    cover_letter    = db.Column(db.Text)
    resume_path     = db.Column(db.String(500))
    submitted_at    = db.Column(db.DateTime, default=datetime.utcnow)
    status          = db.Column(db.String(30), default='submitted')
    notes           = db.Column(db.Text)

class SearchConfig(db.Model):
    id                       = db.Column(db.Integer, primary_key=True)
    keywords                 = db.Column(db.String(500))
    location                 = db.Column(db.String(200))
    remote_only              = db.Column(db.Boolean, default=False)
    blacklisted_companies    = db.Column(db.Text)
    platforms                = db.Column(db.String(200))
    auto_approve             = db.Column(db.Boolean, default=False)
    max_applications_per_run = db.Column(db.Integer, default=10)

    @classmethod
    def get(cls):
        cfg = cls.query.first()
        if not cfg:
            cfg = cls(keywords='software engineer', location='', remote_only=False,
                      blacklisted_companies='', platforms='linkedin,indeed,ziprecruiter',
                      auto_approve=False, max_applications_per_run=10)
            db.session.add(cfg); db.session.commit()
        return cfg

# ══════════════════════════════════════════════════════════════════════════════
# COVER LETTER
# ══════════════════════════════════════════════════════════════════════════════
DEFAULT_CL_TEMPLATE = """\
Dear Hiring Manager,

I am writing to express my strong interest in the {job_title} position at {company}. \
With {years_experience} years of experience in {field}, I am confident my background \
aligns well with what you're looking for.

{custom_paragraph}

I am particularly drawn to {company} because of its reputation and the chance to contribute \
meaningfully to your team. Thank you for considering my application.

Sincerely,
{full_name}
{email}
{phone}
"""

def render_cover_letter(template: str, job: dict, profile: dict) -> str:
    variables = {
        'job_title': job.get('title', ''), 'company': job.get('company', ''),
        'location': job.get('location', ''), 'full_name': profile.get('full_name', ''),
        'first_name': profile.get('first_name', ''), 'last_name': profile.get('last_name', ''),
        'email': profile.get('email', ''), 'phone': profile.get('phone', ''),
        'field': profile.get('field', ''), 'years_experience': profile.get('years_experience', ''),
        'linkedin': profile.get('linkedin', ''), 'website': profile.get('website', ''),
        'custom_paragraph': profile.get('custom_paragraph', ''),
    }
    result = template
    for k, v in variables.items():
        result = result.replace(f'{{{k}}}', str(v) if v else '')
    return re.sub(r'\{[^}]+\}', '', result).strip()

# ══════════════════════════════════════════════════════════════════════════════
# SCRAPERS
# ══════════════════════════════════════════════════════════════════════════════
_HTTP_HEADERS = {
    'User-Agent': ('Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
                   '(KHTML, like Gecko) Chrome/124.0 Safari/537.36'),
    'Accept-Language': 'en-US,en;q=0.9',
}

class BaseScraper(ABC):
    def __init__(self, cfg): self.cfg = cfg

    @abstractmethod
    def search(self, keywords, location, remote_only=False, max_results=25) -> List[Dict]: ...

    def _norm(self, raw: dict) -> dict:
        return {k: (raw.get(k) or '') for k in
                ['title','company','location','salary','url','description',
                 'platform','job_type','external_id']} | {
            'remote': bool(raw.get('remote')),
            'easy_apply': bool(raw.get('easy_apply')),
        }


class LinkedInScraper(BaseScraper):
    def search(self, keywords, location, remote_only=False, max_results=25):
        jobs = []
        try:
            from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
        except ImportError:
            return jobs
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                user_data_dir=self.cfg.get('browser_profile', _DEFAULT_BROWSER_PROFILE),
                headless=False,
                args=['--disable-blink-features=AutomationControlled'],
            )
            page = ctx.new_page()
            try:
                params = f'?keywords={keywords.replace(" ","%20")}&location={location.replace(" ","%20")}&f_LF=f_AL'
                if remote_only: params += '&f_WT=2'
                page.goto(f'https://www.linkedin.com/jobs/search{params}')
                page.wait_for_load_state('networkidle', timeout=15000)
                if 'login' in page.url or 'authwall' in page.url:
                    page.goto('https://www.linkedin.com/login')
                    print('[LinkedIn] Log in in the browser window, then press Enter here.')
                    input()
                    page.goto(f'https://www.linkedin.com/jobs/search{params}')
                    page.wait_for_load_state('networkidle', timeout=15000)
                for card in page.query_selector_all('.job-card-container, .jobs-search-results__list-item')[:max_results]:
                    try:
                        t = card.query_selector('.job-card-list__title, .job-card-container__link')
                        c = card.query_selector('.job-card-container__company-name')
                        l = card.query_selector('.job-card-container__metadata-item')
                        a = card.query_selector('a[href*="/jobs/view/"]')
                        ea = card.query_selector('.job-card-container__apply-method')
                        if not (t and a): continue
                        href = a.get_attribute('href') or ''
                        href = href.split('?')[0]
                        m = re.search(r'/jobs/view/(\d+)', href)
                        jobs.append(self._norm({
                            'title': t.inner_text(), 'company': c.inner_text() if c else '',
                            'location': l.inner_text() if l else '',
                            'url': f'https://www.linkedin.com{href}' if href.startswith('/') else href,
                            'platform': 'linkedin', 'easy_apply': ea is not None,
                            'external_id': m.group(1) if m else None,
                        }))
                    except Exception: continue
            except Exception as e:
                print(f'[LinkedIn] {e}')
            finally:
                ctx.close()
        return jobs


class IndeedScraper(BaseScraper):
    def search(self, keywords, location, remote_only=False, max_results=25):
        jobs = []
        params = {'q': keywords, 'l': location, 'limit': 25}
        if remote_only: params['remotejob'] = '032b3046-06a3-4876-8dfd-474eb5e7ed11'
        try:
            r = http_requests.get('https://www.indeed.com/jobs', params=params, headers=_HTTP_HEADERS, timeout=15)
            soup = BeautifulSoup(r.text, 'html.parser')
            for card in soup.select('div.job_seen_beacon, div.slider_item')[:max_results]:
                t = card.select_one('h2.jobTitle a, a.jcs-JobTitle')
                c = card.select_one('[data-testid="company-name"], .companyName')
                lo = card.select_one('[data-testid="text-location"], .companyLocation')
                s = card.select_one('[data-testid="attribute_snippet_testid"], .salary-snippet-container')
                if not t: continue
                href = t.get('href', '')
                m = re.search(r'jk=([a-z0-9]+)', href)
                jobs.append(self._norm({
                    'title': t.get_text(strip=True), 'company': c.get_text(strip=True) if c else '',
                    'location': lo.get_text(strip=True) if lo else '',
                    'salary': s.get_text(strip=True) if s else None,
                    'url': f'https://www.indeed.com{href}' if href.startswith('/') else href,
                    'platform': 'indeed', 'easy_apply': True,
                    'external_id': m.group(1) if m else None,
                }))
        except Exception as e:
            print(f'[Indeed] {e}')
        return jobs


class ZipRecruiterScraper(BaseScraper):
    def search(self, keywords, location, remote_only=False, max_results=25):
        jobs = []
        params = {'search': keywords, 'location': location}
        if remote_only: params['refine_by_location_type'] = 'only_remote'
        try:
            r = http_requests.get('https://www.ziprecruiter.com/jobs-search', params=params, headers=_HTTP_HEADERS, timeout=15)
            soup = BeautifulSoup(r.text, 'html.parser')
            for card in soup.select('article.job_result, div[data-testid="job-card"]')[:max_results]:
                t = card.select_one('h2.title a, a[data-testid="job-title"]')
                c = card.select_one('a.company_name, [data-testid="job-company"]')
                lo = card.select_one('[data-testid="job-location"], .location')
                s = card.select_one('[data-testid="job-compensation"], .compensation')
                if not t: continue
                url = t.get('href', '')
                if not url.startswith('http'): url = 'https://www.ziprecruiter.com' + url
                jobs.append(self._norm({
                    'title': t.get_text(strip=True), 'company': c.get_text(strip=True) if c else '',
                    'location': lo.get_text(strip=True) if lo else '',
                    'salary': s.get_text(strip=True) if s else None,
                    'url': url, 'platform': 'ziprecruiter', 'easy_apply': True,
                }))
        except Exception as e:
            print(f'[ZipRecruiter] {e}')
        return jobs


class GreenhouseScraper(BaseScraper):
    def search(self, keywords, location, remote_only=False, max_results=25):
        jobs = []
        for token in self.cfg.get('greenhouse_companies', []):
            try:
                data = http_requests.get(
                    f'https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true', timeout=15
                ).json()
                for job in data.get('jobs', [])[:max_results]:
                    title = job.get('title', '')
                    if keywords.lower() not in title.lower(): continue
                    loc = job.get('location', {}).get('name', '')
                    if remote_only and 'remote' not in loc.lower(): continue
                    jobs.append(self._norm({
                        'title': title, 'company': token.replace('-', ' ').title(),
                        'location': loc, 'url': job.get('absolute_url', ''),
                        'platform': 'greenhouse', 'easy_apply': False,
                        'external_id': str(job.get('id', '')),
                        'description': BeautifulSoup(job.get('content', ''), 'html.parser').get_text('\n'),
                    }))
            except Exception as e:
                print(f'[Greenhouse:{token}] {e}')
        return jobs


class LeverScraper(BaseScraper):
    def search(self, keywords, location, remote_only=False, max_results=25):
        jobs = []
        for slug in self.cfg.get('lever_companies', []):
            try:
                params = {'mode': 'json'}
                if remote_only: params['workplaceType'] = 'remote'
                data = http_requests.get(f'https://api.lever.co/v0/postings/{slug}', params=params, timeout=15).json()
                for job in data[:max_results]:
                    title = job.get('text', '')
                    if keywords.lower() not in title.lower(): continue
                    loc = job.get('categories', {}).get('location', '')
                    if remote_only and 'remote' not in loc.lower(): continue
                    jobs.append(self._norm({
                        'title': title, 'company': slug.replace('-', ' ').title(),
                        'location': loc, 'url': job.get('hostedUrl', ''),
                        'platform': 'lever', 'easy_apply': False,
                        'external_id': job.get('id', ''),
                        'description': job.get('descriptionPlain', ''),
                    }))
            except Exception as e:
                print(f'[Lever:{slug}] {e}')
        return jobs


SCRAPERS = {
    'linkedin': LinkedInScraper, 'indeed': IndeedScraper,
    'ziprecruiter': ZipRecruiterScraper, 'greenhouse': GreenhouseScraper,
    'lever': LeverScraper,
}

# ══════════════════════════════════════════════════════════════════════════════
# APPLIERS
# ══════════════════════════════════════════════════════════════════════════════
class BaseApplier(ABC):
    def __init__(self, cfg, ctx): self.cfg = cfg; self.ctx = ctx

    @abstractmethod
    def apply(self, job, cover_letter, resume_path) -> bool: ...

    def _fill_common(self, page, profile):
        for sel, val in {
            'input[name*="first"], input[id*="first"]': profile.get('first_name',''),
            'input[name*="last"], input[id*="last"]':   profile.get('last_name',''),
            'input[name*="email"], input[type="email"]': profile.get('email',''),
            'input[name*="phone"], input[type="tel"]':   profile.get('phone',''),
            'input[name*="linkedin"]': profile.get('linkedin',''),
            'input[name*="website"]':  profile.get('website',''),
        }.items():
            if not val: continue
            try:
                el = page.query_selector(sel)
                if el: el.fill(val)
            except Exception: pass

    def _upload(self, page, resume_path):
        try:
            fi = page.query_selector('input[type="file"]')
            if fi and resume_path and Path(resume_path).exists():
                fi.set_input_files(resume_path)
        except Exception: pass

    def _fill_cl(self, page, cover_letter, selectors):
        if not cover_letter: return
        for sel in selectors:
            try:
                el = page.query_selector(sel)
                if el: el.fill(cover_letter); return
            except Exception: pass


class LinkedInApplier(BaseApplier):
    def apply(self, job, cover_letter, resume_path):
        page = self.ctx.new_page()
        try:
            page.goto(job['url'], timeout=20000)
            page.wait_for_load_state('networkidle', timeout=15000)
            btn = page.query_selector('button.jobs-apply-button, button[aria-label*="Easy Apply"]')
            if not btn: return False
            btn.click(); page.wait_for_selector('.jobs-easy-apply-modal', timeout=10000)
            for _ in range(10):
                time.sleep(1)
                self._fill_common(page, self.cfg.get('profile', {}))
                self._upload(page, resume_path)
                self._fill_cl(page, cover_letter, ['textarea[id*="cover"]', 'textarea[aria-label*="cover"]'])
                if sub := page.query_selector('button[aria-label="Submit application"]'):
                    if not self.cfg.get('dry_run'): sub.click(); time.sleep(2)
                    return True
                nxt = (page.query_selector('button[aria-label*="Continue"]') or
                       page.query_selector('button[aria-label*="Review"]') or
                       page.query_selector('.jobs-easy-apply-footer button.artdeco-button--primary'))
                if nxt: nxt.click()
                else: break
        except Exception as e:
            print(f'[LinkedIn] {e}')
        finally:
            try: page.close()
            except Exception: pass
        return False


class IndeedApplier(BaseApplier):
    def apply(self, job, cover_letter, resume_path):
        page = self.ctx.new_page()
        try:
            page.goto(job['url'], timeout=20000)
            page.wait_for_load_state('networkidle', timeout=15000)
            btn = page.query_selector('#indeedApplyButton, [data-testid="apply-button"]')
            if not btn: return False
            btn.click(); time.sleep(2)
            if len(page.context.pages) > 1: page = page.context.pages[-1]
            for _ in range(8):
                time.sleep(1.5)
                self._fill_common(page, self.cfg.get('profile', {}))
                self._upload(page, resume_path)
                self._fill_cl(page, cover_letter, ['textarea[name*="cover"]'])
                if sub := page.query_selector('button[type="submit"][data-testid*="submit"], button:has-text("Submit")'):
                    if not self.cfg.get('dry_run'): sub.click(); time.sleep(2)
                    return True
                nxt = page.query_selector('button:has-text("Continue"), button:has-text("Next")')
                if nxt: nxt.click()
                else: break
        except Exception as e:
            print(f'[Indeed] {e}')
        finally:
            try: page.close()
            except Exception: pass
        return False


class GenericApplier(BaseApplier):
    _SUBMIT = ['button[type="submit"]','input[type="submit"]',
               'button:has-text("Submit")','button:has-text("Apply")','#submit_app']
    _NEXT   = ['button:has-text("Next")','button:has-text("Continue")']

    def apply(self, job, cover_letter, resume_path):
        page = self.ctx.new_page()
        try:
            page.goto(job['url'], timeout=20000)
            page.wait_for_load_state('networkidle', timeout=15000)
            for sel in ['a:has-text("Apply for this job")','a:has-text("Apply Now")','#apply-button','.postings-btn']:
                try:
                    btn = page.query_selector(sel)
                    if btn: btn.click(); time.sleep(2); break
                except Exception: pass
            for _ in range(8):
                time.sleep(1.5)
                self._fill_common(page, self.cfg.get('profile', {}))
                self._upload(page, resume_path)
                self._fill_cl(page, cover_letter, ['textarea[name*="cover"]','textarea[id*="cover"]','#cover_letter','textarea'])
                for sel in self._SUBMIT:
                    try:
                        btn = page.query_selector(sel)
                        if btn and btn.is_visible():
                            if not self.cfg.get('dry_run'): btn.click(); time.sleep(2)
                            return True
                    except Exception: pass
                advanced = False
                for sel in self._NEXT:
                    try:
                        btn = page.query_selector(sel)
                        if btn and btn.is_visible(): btn.click(); advanced = True; break
                    except Exception: pass
                if not advanced: break
        except Exception as e:
            print(f'[Generic] {e}')
        finally:
            try: page.close()
            except Exception: pass
        return False


APPLIERS = {
    'linkedin': LinkedInApplier, 'indeed': IndeedApplier,
    'ziprecruiter': GenericApplier, 'greenhouse': GenericApplier, 'lever': GenericApplier,
}

# ══════════════════════════════════════════════════════════════════════════════
# RUNNER (background threads)
# ══════════════════════════════════════════════════════════════════════════════
_lock   = threading.Lock()
_status = {'running': False, 'message': 'Idle', 'progress': 0, 'total': 0}

def _upd(msg, progress=None, total=None):
    _status['message'] = msg
    if progress is not None: _status['progress'] = progress
    if total    is not None: _status['total']    = total

def get_status(): return dict(_status)

def _load_cfg():
    if CONFIG_PATH.exists():
        return yaml.safe_load(CONFIG_PATH.read_text()) or {}
    return {}

def _load_cl():
    return CL_PATH.read_text() if CL_PATH.exists() else DEFAULT_CL_TEMPLATE

def scrape_jobs(flask_app):
    if _status['running']: return
    threading.Thread(target=_scrape_worker, args=(flask_app,), daemon=True).start()

def _scrape_worker(flask_app):
    _status['running'] = True
    try:
        with flask_app.app_context():
            cfg = SearchConfig.get()
            platforms = [p.strip() for p in (cfg.platforms or '').split(',') if p.strip()]
            sc = _load_cfg()
            blacklist = {c.strip().lower() for c in (cfg.blacklisted_companies or '').splitlines() if c.strip()}
            new_count = 0
            for i, platform in enumerate(platforms):
                _upd(f'Searching {platform}...', progress=i, total=len(platforms))
                cls = SCRAPERS.get(platform)
                if not cls: continue
                try:
                    jobs = cls(sc).search(cfg.keywords or '', cfg.location or '', remote_only=cfg.remote_only)
                except Exception as e:
                    print(f'[Runner:{platform}] {e}'); continue
                for jd in jobs:
                    if jd['company'].lower() in blacklist: continue
                    if Job.query.filter_by(url=jd['url']).first(): continue
                    job = Job(status='approved' if cfg.auto_approve else 'pending',
                              **{k: v for k, v in jd.items() if hasattr(Job, k)})
                    db.session.add(job); new_count += 1
            db.session.commit()
            _upd(f'Done — {new_count} new jobs found.', progress=len(platforms), total=len(platforms))
    except Exception as e:
        _upd(f'Error: {e}')
    finally:
        _status['running'] = False

def apply_approved(flask_app):
    if _status['running']: return
    threading.Thread(target=_apply_worker, args=(flask_app,), daemon=True).start()

def _apply_worker(flask_app):
    _status['running'] = True
    try:
        with flask_app.app_context():
            cfg = SearchConfig.get()
            approved = Job.query.filter_by(status='approved').all()[:cfg.max_applications_per_run or 10]
            _upd(f'Applying to {len(approved)} jobs...', progress=0, total=len(approved))
            if not approved: _upd('No approved jobs to apply to.'); return
            sc = _load_cfg(); profile = sc.get('profile', {}); resume_path = sc.get('resume_path', '')
            cl_template = _load_cl()
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                ctx = p.chromium.launch_persistent_context(
                    user_data_dir=sc.get('browser_profile', _DEFAULT_BROWSER_PROFILE),
                    headless=False,
                    args=['--disable-blink-features=AutomationControlled'],
                )
                for i, job in enumerate(approved):
                    _upd(f'Applying to {job.title} @ {job.company}...', progress=i + 1)
                    cl = render_cover_letter(cl_template, job.__dict__, profile)
                    applier_cls = APPLIERS.get(job.platform, GenericApplier)
                    try:
                        success = applier_cls(sc, ctx).apply(job.__dict__, cl, resume_path)
                    except Exception as e:
                        print(f'[Apply] {e}'); success = False
                    job.status = 'applied' if success else 'failed'
                    if success: job.applied_at = datetime.utcnow()
                    db.session.add(Application(job_id=job.id, cover_letter=cl, resume_path=resume_path,
                                               status='submitted' if success else 'failed'))
                    db.session.commit()
                ctx.close()
            _upd(f'Finished. Applied to {sum(1 for j in approved if j.status == "applied")} jobs.')
    except Exception as e:
        _upd(f'Error: {e}')
    finally:
        _status['running'] = False

# ══════════════════════════════════════════════════════════════════════════════
# TEMPLATES (all inline)
# ══════════════════════════════════════════════════════════════════════════════
_CSS = """
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{--bg:#0f1117;--surface:#1a1d27;--surface2:#22263a;--border:#2e3350;--text:#e2e8f0;
  --muted:#8892a4;--accent:#5b8dee;--green:#22c55e;--red:#ef4444;--yellow:#f59e0b;
  --font:'Inter','Segoe UI',system-ui,sans-serif}
body{display:flex;min-height:100vh;background:var(--bg);color:var(--text);font-family:var(--font);font-size:14px}
.sidebar{width:200px;min-height:100vh;background:var(--surface);border-right:1px solid var(--border);
  display:flex;flex-direction:column;padding:20px 0;position:fixed;top:0;left:0;height:100%}
.brand{padding:0 20px 24px;font-size:16px;font-weight:700;color:var(--accent)}
.sidebar a{display:block;padding:10px 20px;color:var(--muted);text-decoration:none;
  transition:all .15s;border-left:3px solid transparent}
.sidebar a:hover,.sidebar a.active{color:var(--text);background:var(--surface2);border-left-color:var(--accent)}
.content{margin-left:200px;padding:32px;flex:1;max-width:1100px}
.alert{padding:12px 16px;border-radius:8px;margin-bottom:16px}
.alert-success{background:rgba(34,197,94,.15);border:1px solid rgba(34,197,94,.3);color:var(--green)}
.alert-error{background:rgba(239,68,68,.15);border:1px solid rgba(239,68,68,.3);color:var(--red)}
.page-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:28px}
.page-header h1{font-size:22px;font-weight:700}
.actions,.filter-bar{display:flex;gap:10px;flex-wrap:wrap}
.btn{padding:8px 16px;border-radius:6px;border:1px solid transparent;cursor:pointer;font-size:13px;
  font-weight:500;transition:all .15s;text-decoration:none;display:inline-block}
.btn-primary{background:var(--accent);color:#fff}.btn-primary:hover{background:#4a7de0}
.btn-success{background:var(--green);color:#fff}.btn-success:hover{background:#16a34a}
.btn-danger{background:var(--red);color:#fff}.btn-danger:hover{background:#dc2626}
.btn-ghost{background:transparent;border-color:var(--border);color:var(--muted)}
.btn-ghost:hover{border-color:var(--accent);color:var(--accent)}
.btn-sm{padding:5px 10px;font-size:12px}
.stats-grid{display:grid;grid-template-columns:repeat(5,1fr);gap:16px;margin-bottom:32px}
.stat-card{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:20px;text-align:center}
.stat-number{font-size:36px;font-weight:700;margin-bottom:4px}
.stat-label{font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.05em}
.stat-link{display:block;margin-top:10px;font-size:12px;color:var(--accent);text-decoration:none}
.stat-card.pending .stat-number{color:var(--yellow)}.stat-card.approved .stat-number{color:var(--green)}
.stat-card.applied .stat-number{color:var(--accent)}.stat-card.failed .stat-number{color:var(--red)}
.status-bar{background:var(--surface2);border:1px solid var(--border);border-radius:8px;
  padding:12px 16px;margin-bottom:24px}
.status-bar-inner{display:flex;align-items:center;gap:10px}
.spinner{width:14px;height:14px;border:2px solid var(--border);border-top-color:var(--accent);
  border-radius:50%;animation:spin .8s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
.progress-track{flex:1;height:6px;background:var(--border);border-radius:3px;overflow:hidden}
.progress-fill{height:100%;background:var(--accent);transition:width .3s}
.table{width:100%;border-collapse:collapse;background:var(--surface);border-radius:12px;overflow:hidden}
.table th{background:var(--surface2);padding:10px 14px;text-align:left;font-size:12px;
  color:var(--muted);text-transform:uppercase;letter-spacing:.05em}
.table td{padding:10px 14px;border-top:1px solid var(--border)}
.table td a{color:var(--accent);text-decoration:none}.table td a:hover{text-decoration:underline}
.empty{text-align:center;color:var(--muted);padding:32px!important}
.empty-state{text-align:center;color:var(--muted);padding:64px}
.badge{display:inline-block;padding:2px 8px;border-radius:4px;font-size:11px;
  font-weight:600;text-transform:uppercase;letter-spacing:.04em}
.badge-linkedin{background:rgba(10,102,194,.25);color:#60a5fa}
.badge-indeed{background:rgba(37,99,235,.2);color:#93c5fd}
.badge-ziprecruiter{background:rgba(59,130,246,.2);color:#bfdbfe}
.badge-greenhouse{background:rgba(34,197,94,.2);color:#86efac}
.badge-lever{background:rgba(168,85,247,.2);color:#d8b4fe}
.badge-pending{background:rgba(245,158,11,.2);color:var(--yellow)}
.badge-approved{background:rgba(34,197,94,.2);color:var(--green)}
.badge-applied{background:rgba(91,141,238,.2);color:var(--accent)}
.badge-rejected{background:rgba(239,68,68,.2);color:var(--red)}
.badge-failed{background:rgba(239,68,68,.15);color:#f87171}
.badge-easy{background:rgba(34,197,94,.15);color:var(--green)}
.badge-remote{background:rgba(168,85,247,.15);color:#c084fc}
.bulk-bar{display:flex;align-items:center;gap:10px;margin-bottom:12px}
.select-all-label{margin-left:auto;display:flex;align-items:center;gap:6px;color:var(--muted);cursor:pointer}
.job-list{display:flex;flex-direction:column;gap:10px}
.job-card{display:flex;gap:14px;background:var(--surface);border:1px solid var(--border);
  border-radius:10px;padding:16px;align-items:flex-start}
.job-card:hover{border-color:var(--accent)}
.job-card-check{padding-top:3px}
.job-card-body{flex:1}
.job-card-title{font-size:15px;font-weight:600;margin-bottom:4px;display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.job-card-title a{color:var(--text);text-decoration:none}.job-card-title a:hover{color:var(--accent)}
.job-card-meta{color:var(--muted);font-size:13px;display:flex;align-items:center;gap:8px;margin-bottom:8px;flex-wrap:wrap}
.job-card-desc{font-size:13px;color:var(--muted);line-height:1.5}
.job-card-actions{display:flex;flex-direction:column;gap:6px}
.settings-form{max-width:800px}
.settings-section{background:var(--surface);border:1px solid var(--border);border-radius:12px;
  padding:24px;margin-bottom:20px}
.settings-section h2{font-size:16px;font-weight:600;margin-bottom:6px}
.hint{color:var(--muted);font-size:12px;margin-bottom:16px}
.form-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:14px}
.form-group{display:flex;flex-direction:column;gap:5px}
.form-group label{font-size:12px;color:var(--muted);font-weight:500}
input[type=text],input[type=email],input[type=url],input[type=tel],input[type=number],textarea,select{
  background:var(--surface2);border:1px solid var(--border);border-radius:6px;
  color:var(--text);padding:8px 12px;font-size:13px;width:100%;
  font-family:var(--font);transition:border-color .15s}
input:focus,textarea:focus,select:focus{outline:none;border-color:var(--accent)}
.code-area{font-family:'Fira Code','Courier New',monospace;font-size:12px;line-height:1.6}
.form-row{display:flex;gap:24px;margin-bottom:14px;flex-wrap:wrap}
.checkbox-label{display:flex;align-items:center;gap:6px;cursor:pointer}
.form-actions{padding-top:8px}
.inline-form{display:inline}.status-select{padding:4px 8px;width:auto}
.notes-area{width:100%;min-height:80px;margin-bottom:8px}
.cover-letter-details{margin-top:10px}
.cover-letter-details summary{cursor:pointer;color:var(--muted);font-size:12px}
.cover-letter-pre{white-space:pre-wrap;font-size:12px;color:var(--muted);background:var(--bg);
  padding:12px;border-radius:6px;margin-top:8px}
@media(max-width:768px){.stats-grid{grid-template-columns:repeat(2,1fr)}.form-grid{grid-template-columns:1fr}
  .sidebar{display:none}.content{margin-left:0}}
"""

_JS = """
const selectAll = document.getElementById('selectAll');
if (selectAll) selectAll.addEventListener('change', () => {
  document.querySelectorAll('.job-check').forEach(cb => cb.checked = selectAll.checked);
});
let polling = null;
function startScrape() {
  fetch('/api/scrape', {method:'POST'}).then(r=>r.json()).then(d=>{
    if (d.error){alert(d.error);return;} showBar(); startPolling();
  });
}
function startApply() {
  if (!confirm('Apply to all approved jobs now?')) return;
  fetch('/api/apply', {method:'POST'}).then(r=>r.json()).then(d=>{
    if (d.error){alert(d.error);return;} showBar(); startPolling();
  });
}
function showBar(){const b=document.getElementById('statusBar');if(b)b.style.display='block';}
function startPolling(){
  if (polling) clearInterval(polling);
  polling = setInterval(()=>{
    fetch('/api/status').then(r=>r.json()).then(s=>{
      const m=document.getElementById('statusMsg'); if(m) m.textContent=s.message;
      const f=document.querySelector('.progress-fill');
      if(f&&s.total) f.style.width=Math.round(s.progress/s.total*100)+'%';
      if(!s.running){clearInterval(polling);polling=null;setTimeout(()=>location.reload(),1500);}
    });
  },1500);
}
function toggleNotes(id){const r=document.getElementById('notes-'+id);if(r)r.style.display=r.style.display==='none'?'table-row':'none';}
(function(){const b=document.getElementById('statusBar');if(b&&b.style.display!=='none')startPolling();})();
"""

_BASE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{% block title %}Job Applier{% endblock %}</title>
  <style>""" + _CSS + """</style>
</head>
<body>
  <nav class="sidebar">
    <div class="brand">Job Applier</div>
    <a href="/" class="{{ 'active' if active=='dashboard' }}">Dashboard</a>
    <a href="/jobs" class="{{ 'active' if active=='jobs' }}">Job Queue</a>
    <a href="/applications" class="{{ 'active' if active=='applications' }}">Applications</a>
    <a href="/settings" class="{{ 'active' if active=='settings' }}">Settings</a>
  </nav>
  <main class="content">
    {% with messages = get_flashed_messages(with_categories=true) %}
      {% for cat, msg in messages %}
        <div class="alert alert-{{ cat }}">{{ msg }}</div>
      {% endfor %}
    {% endwith %}
    {% block content %}{% endblock %}
  </main>
  <script>""" + _JS + """</script>
</body>
</html>"""

_DASHBOARD = _BASE.replace("{% block content %}{% endblock %}", """{% block content %}
<div class="page-header">
  <h1>Dashboard</h1>
  <div class="actions">
    <button class="btn btn-primary" onclick="startScrape()">Find Jobs</button>
    <button class="btn btn-success" onclick="startApply()">Apply to Approved</button>
  </div>
</div>
<div class="stats-grid">
  <div class="stat-card"><div class="stat-number">{{total}}</div><div class="stat-label">Total Found</div></div>
  <div class="stat-card pending"><div class="stat-number">{{pending}}</div><div class="stat-label">Pending Review</div><a href="/jobs?status=pending" class="stat-link">Review</a></div>
  <div class="stat-card approved"><div class="stat-number">{{approved}}</div><div class="stat-label">Approved</div><a href="/jobs?status=approved" class="stat-link">View</a></div>
  <div class="stat-card applied"><div class="stat-number">{{applied}}</div><div class="stat-label">Applied</div><a href="/applications" class="stat-link">View</a></div>
  <div class="stat-card failed"><div class="stat-number">{{failed}}</div><div class="stat-label">Failed</div><a href="/jobs?status=failed" class="stat-link">View</a></div>
</div>
<div class="status-bar" id="statusBar" style="{{'' if status.running else 'display:none'}}">
  <div class="status-bar-inner">
    <span class="spinner"></span>
    <span id="statusMsg">{{status.message}}</span>
    {% if status.total %}<div class="progress-track"><div class="progress-fill" style="width:{{(status.progress/status.total*100)|int}}%"></div></div>{% endif %}
  </div>
</div>
<h2 style="margin-bottom:12px">Recent Jobs</h2>
<table class="table">
  <thead><tr><th>Title</th><th>Company</th><th>Platform</th><th>Status</th><th>Found</th></tr></thead>
  <tbody>
    {% for job in recent %}
    <tr>
      <td><a href="{{job.url}}" target="_blank">{{job.title}}</a></td>
      <td>{{job.company}}</td>
      <td><span class="badge badge-{{job.platform}}">{{job.platform}}</span></td>
      <td><span class="badge badge-{{job.status}}">{{job.status}}</span></td>
      <td>{{job.found_at.strftime('%b %d')}}</td>
    </tr>
    {% else %}
    <tr><td colspan="5" class="empty">No jobs yet — click "Find Jobs" to start.</td></tr>
    {% endfor %}
  </tbody>
</table>
{% endblock %}""")

_JOBS = _BASE.replace("{% block content %}{% endblock %}", """{% block content %}
<div class="page-header">
  <h1>Job Queue</h1>
  <div class="filter-bar">
    {% for s in ['pending','approved','rejected','applied','failed'] %}
    <a href="/jobs?status={{s}}" class="btn {{'btn-primary' if status_filter==s else 'btn-ghost'}}">{{s.title()}}</a>
    {% endfor %}
  </div>
</div>
{% if jobs %}
<form method="post" action="/jobs/bulk">
  <div class="bulk-bar">
    <button type="submit" name="action" value="approve" class="btn btn-success btn-sm">Approve Selected</button>
    <button type="submit" name="action" value="reject" class="btn btn-danger btn-sm">Reject Selected</button>
    <label class="select-all-label"><input type="checkbox" id="selectAll"> Select all</label>
  </div>
  <div class="job-list">
    {% for job in jobs %}
    <div class="job-card">
      <div class="job-card-check"><input type="checkbox" name="job_ids" value="{{job.id}}" class="job-check"></div>
      <div class="job-card-body">
        <div class="job-card-title">
          <a href="{{job.url}}" target="_blank">{{job.title}}</a>
          {% if job.easy_apply %}<span class="badge badge-easy">Easy Apply</span>{% endif %}
          {% if job.remote %}<span class="badge badge-remote">Remote</span>{% endif %}
        </div>
        <div class="job-card-meta">
          <strong>{{job.company}}</strong>{% if job.location %} · {{job.location}}{% endif %}
          {% if job.salary %} · {{job.salary}}{% endif %}
          <span class="badge badge-{{job.platform}}">{{job.platform}}</span>
        </div>
        {% if job.description %}<div class="job-card-desc">{{job.description[:300]}}{% if job.description|length > 300 %}…{% endif %}</div>{% endif %}
      </div>
      <div class="job-card-actions">
        {% if job.status != 'approved' %}
        <form method="post" action="/jobs/{{job.id}}/approve" style="display:inline"><button class="btn btn-success btn-sm">Approve</button></form>
        {% endif %}
        {% if job.status != 'rejected' %}
        <form method="post" action="/jobs/{{job.id}}/reject" style="display:inline"><button class="btn btn-danger btn-sm">Reject</button></form>
        {% endif %}
      </div>
    </div>
    {% endfor %}
  </div>
</form>
{% else %}
<div class="empty-state">No jobs with status "{{status_filter}}".</div>
{% endif %}
{% endblock %}""")

_APPLICATIONS = _BASE.replace("{% block content %}{% endblock %}", """{% block content %}
<div class="page-header"><h1>Applications</h1></div>
{% if applications %}
<table class="table">
  <thead><tr><th>Job</th><th>Company</th><th>Platform</th><th>Status</th><th>Applied</th><th>Actions</th></tr></thead>
  <tbody>
    {% for app in applications %}
    <tr>
      <td><a href="{{app.job.url}}" target="_blank">{{app.job.title}}</a></td>
      <td>{{app.job.company}}</td>
      <td><span class="badge badge-{{app.job.platform}}">{{app.job.platform}}</span></td>
      <td>
        <form method="post" action="/applications/{{app.id}}/status" class="inline-form">
          <select name="status" class="status-select" onchange="this.form.submit()">
            {% for s in ['submitted','confirmed','interviewing','offer','rejected','withdrawn'] %}
            <option value="{{s}}" {{'selected' if app.status==s}}>{{s.title()}}</option>
            {% endfor %}
          </select>
        </form>
      </td>
      <td>{{app.submitted_at.strftime('%b %d, %Y')}}</td>
      <td><button class="btn btn-ghost btn-sm" onclick="toggleNotes({{app.id}})">Notes</button></td>
    </tr>
    <tr id="notes-{{app.id}}" style="display:none">
      <td colspan="6">
        <form method="post" action="/applications/{{app.id}}/status">
          <input type="hidden" name="status" value="{{app.status}}">
          <textarea name="notes" class="notes-area" placeholder="Interview notes, follow-up dates…">{{app.notes or ''}}</textarea>
          <button type="submit" class="btn btn-primary btn-sm">Save Notes</button>
          <details class="cover-letter-details">
            <summary>Cover Letter</summary>
            <pre class="cover-letter-pre">{{app.cover_letter or '(none)'}}</pre>
          </details>
        </form>
      </td>
    </tr>
    {% endfor %}
  </tbody>
</table>
{% else %}
<div class="empty-state">No applications yet. Approve some jobs and click "Apply to Approved".</div>
{% endif %}
{% endblock %}""")

_SETTINGS = _BASE.replace("{% block content %}{% endblock %}", """{% block content %}
<div class="page-header"><h1>Settings</h1></div>
<form method="post" enctype="multipart/form-data" class="settings-form">
  <section class="settings-section">
    <h2>Your Profile</h2>
    <p class="hint">Used to fill application forms and cover letters.</p>
    <div class="form-grid">
      {% for field, label in [('first_name','First Name'),('last_name','Last Name'),('full_name','Full Name (cover letter)'),('email','Email'),('phone','Phone'),('linkedin','LinkedIn URL'),('website','Website / Portfolio'),('field','Field / Specialty'),('years_experience','Years of Experience')] %}
      <div class="form-group"><label>{{label}}</label><input type="text" name="{{field}}" value="{{profile.get(field,'')}}"></div>
      {% endfor %}
    </div>
    <div class="form-group"><label>Custom Paragraph ({custom_paragraph} in template)</label>
    <textarea name="custom_paragraph" rows="4">{{profile.get('custom_paragraph','')}}</textarea></div>
  </section>
  <section class="settings-section">
    <h2>Resume</h2>
    {% if resume_path %}<p class="hint">Current: <code>{{resume_path}}</code></p>{% endif %}
    <input type="file" name="resume" accept=".pdf,.doc,.docx">
  </section>
  <section class="settings-section">
    <h2>Cover Letter Template</h2>
    <p class="hint">Variables: {job_title} {company} {location} {full_name} {email} {phone} {field} {years_experience} {linkedin} {website} {custom_paragraph}</p>
    <textarea name="cover_template" rows="16" class="code-area">{{cover_template}}</textarea>
  </section>
  <section class="settings-section">
    <h2>Job Search</h2>
    <div class="form-grid">
      <div class="form-group"><label>Keywords</label><input type="text" name="keywords" value="{{sc.keywords or ''}}"></div>
      <div class="form-group"><label>Location (blank = any)</label><input type="text" name="location" value="{{sc.location or ''}}"></div>
      <div class="form-group"><label>Max applications per run</label><input type="number" name="max_applications_per_run" value="{{sc.max_applications_per_run or 10}}" min="1" max="100"></div>
    </div>
    <div class="form-group"><label>Platforms (comma-separated: linkedin, indeed, ziprecruiter, greenhouse, lever)</label>
    <input type="text" name="platforms" value="{{sc.platforms or ''}}"></div>
    <div class="form-row">
      <label class="checkbox-label"><input type="checkbox" name="remote_only" {{'checked' if sc.remote_only}}> Remote only</label>
      <label class="checkbox-label"><input type="checkbox" name="auto_approve" {{'checked' if sc.auto_approve}}> Auto-approve all found jobs</label>
      <label class="checkbox-label"><input type="checkbox" name="dry_run" {{'checked' if dry_run}}> Dry run (don't submit)</label>
    </div>
    <div class="form-group"><label>Blacklisted Companies (one per line)</label>
    <textarea name="blacklisted_companies" rows="4">{{sc.blacklisted_companies or ''}}</textarea></div>
  </section>
  <section class="settings-section">
    <h2>Greenhouse Companies</h2>
    <p class="hint">Board tokens, one per line (e.g. <code>stripe</code>)</p>
    <textarea name="greenhouse_companies" rows="4">{{greenhouse_companies}}</textarea>
  </section>
  <section class="settings-section">
    <h2>Lever Companies</h2>
    <p class="hint">Company slugs, one per line (e.g. <code>linear</code>)</p>
    <textarea name="lever_companies" rows="4">{{lever_companies}}</textarea>
  </section>
  <section class="settings-section">
    <h2>Browser Profile Path</h2>
    <p class="hint">Persistent Chromium profile — keeps you logged in to LinkedIn, Indeed, etc.</p>
    <input type="text" name="browser_profile" value="{{browser_profile}}">
  </section>
  <div class="form-actions"><button type="submit" class="btn btn-primary">Save Settings</button></div>
</form>
{% endblock %}""")

# ══════════════════════════════════════════════════════════════════════════════
# FLASK APP
# ══════════════════════════════════════════════════════════════════════════════
app = Flask(__name__)
app.secret_key = 'job-applier-portable-secret'
app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{DB_PATH}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = str(UPLOAD_DIR)
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024
db.init_app(app)

with app.app_context():
    db.create_all()


def _load_app_cfg():
    return yaml.safe_load(CONFIG_PATH.read_text()) if CONFIG_PATH.exists() else {}

def _save_app_cfg(data):
    CONFIG_PATH.write_text(yaml.dump(data, default_flow_style=False))

def _load_cl_template():
    return CL_PATH.read_text() if CL_PATH.exists() else DEFAULT_CL_TEMPLATE

def _t(template_str, **kwargs):
    return render_template_string(template_str, **kwargs)


@app.route('/')
def dashboard():
    return _t(_DASHBOARD, active='dashboard',
               total=Job.query.count(),
               pending=Job.query.filter_by(status='pending').count(),
               approved=Job.query.filter_by(status='approved').count(),
               applied=Job.query.filter_by(status='applied').count(),
               failed=Job.query.filter_by(status='failed').count(),
               recent=Job.query.order_by(Job.found_at.desc()).limit(10).all(),
               status=get_status())


@app.route('/jobs')
def jobs():
    sf = request.args.get('status', 'pending')
    pf = request.args.get('platform', '')
    q = Job.query.filter_by(status=sf) if sf else Job.query
    if pf: q = q.filter_by(platform=pf)
    return _t(_JOBS, active='jobs', jobs=q.order_by(Job.found_at.desc()).all(),
              status_filter=sf, platform_filter=pf)


@app.route('/jobs/<int:job_id>/approve', methods=['POST'])
def approve_job(job_id):
    job = Job.query.get_or_404(job_id); job.status = 'approved'; db.session.commit()
    return redirect(request.referrer or url_for('jobs'))


@app.route('/jobs/<int:job_id>/reject', methods=['POST'])
def reject_job(job_id):
    job = Job.query.get_or_404(job_id); job.status = 'rejected'; db.session.commit()
    return redirect(request.referrer or url_for('jobs'))


@app.route('/jobs/bulk', methods=['POST'])
def bulk_action():
    action = request.form.get('action'); ids = request.form.getlist('job_ids')
    if action and ids:
        status = 'approved' if action == 'approve' else 'rejected'
        Job.query.filter(Job.id.in_([int(i) for i in ids])).update({'status': status}, synchronize_session=False)
        db.session.commit()
        flash(f'{len(ids)} jobs {status}.', 'success')
    return redirect(url_for('jobs'))


@app.route('/applications')
def applications():
    return _t(_APPLICATIONS, active='applications',
               applications=Application.query.order_by(Application.submitted_at.desc()).all())


@app.route('/applications/<int:app_id>/status', methods=['POST'])
def update_app_status(app_id):
    a = Application.query.get_or_404(app_id)
    a.status = request.form.get('status', a.status)
    a.notes  = request.form.get('notes', a.notes)
    db.session.commit()
    return redirect(url_for('applications'))


@app.route('/settings', methods=['GET', 'POST'])
def settings():
    sc = SearchConfig.get()
    cfg = _load_app_cfg()
    profile = cfg.get('profile', {})

    if request.method == 'POST':
        sc.keywords = request.form.get('keywords', '')
        sc.location = request.form.get('location', '')
        sc.remote_only = 'remote_only' in request.form
        sc.blacklisted_companies = request.form.get('blacklisted_companies', '')
        sc.platforms = request.form.get('platforms', '')
        sc.auto_approve = 'auto_approve' in request.form
        sc.max_applications_per_run = int(request.form.get('max_applications_per_run', 10))
        db.session.commit()

        CL_PATH.write_text(request.form.get('cover_template', DEFAULT_CL_TEMPLATE))

        cfg['profile'] = {f: request.form.get(f, '') for f in
                          ['full_name','first_name','last_name','email','phone',
                           'linkedin','website','field','years_experience','custom_paragraph']}
        cfg['browser_profile'] = request.form.get('browser_profile', _DEFAULT_BROWSER_PROFILE)
        cfg['dry_run'] = 'dry_run' in request.form
        cfg['greenhouse_companies'] = [c.strip() for c in request.form.get('greenhouse_companies','').splitlines() if c.strip()]
        cfg['lever_companies']      = [c.strip() for c in request.form.get('lever_companies','').splitlines() if c.strip()]

        if 'resume' in request.files:
            f = request.files['resume']
            if f and f.filename:
                path = UPLOAD_DIR / secure_filename(f.filename)
                f.save(str(path))
                cfg['resume_path'] = str(path)

        _save_app_cfg(cfg)
        flash('Settings saved.', 'success')
        return redirect(url_for('settings'))

    return _t(_SETTINGS, active='settings', sc=sc, profile=profile,
               cover_template=_load_cl_template(),
               resume_path=cfg.get('resume_path', ''),
               dry_run=cfg.get('dry_run', False),
               browser_profile=cfg.get('browser_profile', _DEFAULT_BROWSER_PROFILE),
               greenhouse_companies='\n'.join(cfg.get('greenhouse_companies', [])),
               lever_companies='\n'.join(cfg.get('lever_companies', [])))


@app.route('/api/scrape', methods=['POST'])
def api_scrape():
    if get_status()['running']: return jsonify({'error': 'Already running'}), 409
    scrape_jobs(app); return jsonify({'ok': True})


@app.route('/api/apply', methods=['POST'])
def api_apply():
    if get_status()['running']: return jsonify({'error': 'Already running'}), 409
    apply_approved(app); return jsonify({'ok': True})


@app.route('/api/status')
def api_status():
    return jsonify(get_status())


# ══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════
if __name__ == '__main__':
    # Install Playwright browser on first run (works on Windows, macOS, Linux)
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            exe = p.chromium.executable_path
            if not exe or not Path(exe).exists():
                raise FileNotFoundError(exe)
    except Exception:
        print('Installing Playwright Chromium browser (one-time, ~150 MB)...')
        subprocess.check_call([sys.executable, '-m', 'playwright', 'install', 'chromium'])

    port = int(os.environ.get('PORT', 5055))
    print(f'\n  Job Applier  →  http://localhost:{port}')
    print(f'  Platform     →  {platform.system()} {platform.release()}')
    print(f'  Browser data →  {_DEFAULT_BROWSER_PROFILE}\n')
    app.run(debug=False, port=port, use_reloader=False)
