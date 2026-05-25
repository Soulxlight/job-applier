import os
import secrets
import yaml
from flask import Flask, render_template, request, redirect, url_for, jsonify, flash, session
from werkzeug.utils import secure_filename
from database import db, Job, Application, SearchConfig
import runner
from cover_letter import DEFAULT_TEMPLATE
from auth import (login_required, is_logged_in, verify_password,
                  rate_limited, clear_rate_limit, csrf_token, validate_csrf)

BASE_DIR          = os.path.dirname(os.path.abspath(__file__))
# On Render a persistent disk is mounted at /data; fall back to local dir
_DATA_DIR         = os.environ.get('RENDER_DATA_DIR', '/data') \
                    if os.path.isdir(os.environ.get('RENDER_DATA_DIR', '/data')) \
                    else BASE_DIR
UPLOAD_FOLDER     = os.path.join(_DATA_DIR, 'uploads')
CONFIG_PATH       = os.path.join(_DATA_DIR, 'config.yaml')
COVER_LETTER_PATH = os.path.join(_DATA_DIR, 'cover_letter_template.txt')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)


def _load_config():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH) as f:
            return yaml.safe_load(f) or {}
    return {}


def _save_config(data):
    with open(CONFIG_PATH, 'w') as f:
        yaml.dump(data, f, default_flow_style=False)


def _get_or_create_secret_key() -> str:
    # Prefer environment variable (needed on Render where filesystem is ephemeral)
    if os.environ.get('SECRET_KEY'):
        return os.environ['SECRET_KEY']
    cfg = _load_config()
    if 'secret_key' not in cfg:
        cfg['secret_key'] = secrets.token_hex(32)
        _save_config(cfg)
    return cfg['secret_key']


app = Flask(__name__)
app.secret_key = _get_or_create_secret_key()
app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{os.path.join(_DATA_DIR, "jobs.db")}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

db.init_app(app)

with app.app_context():
    db.create_all()

# Make csrf_token() callable from every template
app.jinja_env.globals['csrf_token'] = csrf_token


# ── Auth routes ───────────────────────────────────────────────────────────────

@app.route('/login', methods=['GET', 'POST'])
def login():
    if is_logged_in():
        return redirect(url_for('dashboard'))

    error = None
    next_url = request.args.get('next') or request.form.get('next', '')

    if request.method == 'POST':
        if rate_limited():
            error = 'rate_limited'
        else:
            validate_csrf()
            password  = request.form.get('password', '')
            cfg       = _load_config()
            pw_hash   = os.environ.get('PASSWORD_HASH') or cfg.get('password_hash', '')

            if not pw_hash:
                flash('No password set. Run: python set_password.py', 'error')
            elif verify_password(password, pw_hash):
                clear_rate_limit()
                session.clear()
                session['authenticated'] = True
                session.permanent = False
                target = next_url if next_url and next_url.startswith('/') else url_for('dashboard')
                return redirect(target)
            else:
                error = 'wrong_password'

    token = csrf_token()
    return render_template('login.html', error=error, csrf_token=token,
                           next=next_url if next_url.startswith('/') else '')


@app.route('/logout', methods=['POST'])
def logout():
    validate_csrf()
    session.clear()
    return redirect(url_for('login'))


# ── Protected routes ──────────────────────────────────────────────────────────

@app.route('/')
@login_required
def dashboard():
    total   = Job.query.count()
    pending = Job.query.filter_by(status='pending').count()
    approved = Job.query.filter_by(status='approved').count()
    applied  = Job.query.filter_by(status='applied').count()
    failed   = Job.query.filter_by(status='failed').count()
    recent   = Job.query.order_by(Job.found_at.desc()).limit(10).all()
    status   = runner.get_status()
    return render_template('dashboard.html',
                           total=total, pending=pending, approved=approved,
                           applied=applied, failed=failed,
                           recent=recent, status=status)


@app.route('/jobs')
@login_required
def jobs():
    status_filter   = request.args.get('status', 'pending')
    platform_filter = request.args.get('platform', '')
    q = Job.query.filter_by(status=status_filter) if status_filter else Job.query
    if platform_filter:
        q = q.filter_by(platform=platform_filter)
    jobs_list = q.order_by(Job.found_at.desc()).all()
    return render_template('jobs.html', jobs=jobs_list,
                           status_filter=status_filter, platform_filter=platform_filter)


@app.route('/jobs/<int:job_id>/approve', methods=['POST'])
@login_required
def approve_job(job_id):
    job = Job.query.get_or_404(job_id)
    job.status = 'approved'
    db.session.commit()
    return redirect(request.referrer or url_for('jobs'))


@app.route('/jobs/<int:job_id>/reject', methods=['POST'])
@login_required
def reject_job(job_id):
    job = Job.query.get_or_404(job_id)
    job.status = 'rejected'
    db.session.commit()
    return redirect(request.referrer or url_for('jobs'))


@app.route('/jobs/bulk', methods=['POST'])
@login_required
def bulk_action():
    action  = request.form.get('action')
    job_ids = request.form.getlist('job_ids')
    if action and job_ids:
        status = 'approved' if action == 'approve' else 'rejected'
        Job.query.filter(Job.id.in_([int(i) for i in job_ids])).update(
            {'status': status}, synchronize_session=False)
        db.session.commit()
        flash(f'{len(job_ids)} jobs {status}.', 'success')
    return redirect(url_for('jobs'))


@app.route('/applications')
@login_required
def applications():
    apps = Application.query.order_by(Application.submitted_at.desc()).all()
    return render_template('applications.html', applications=apps)


@app.route('/applications/<int:app_id>/status', methods=['POST'])
@login_required
def update_app_status(app_id):
    application = Application.query.get_or_404(app_id)
    application.status = request.form.get('status', application.status)
    application.notes  = request.form.get('notes', application.notes)
    db.session.commit()
    return redirect(url_for('applications'))


@app.route('/settings', methods=['GET', 'POST'])
@login_required
def settings():
    search_config = SearchConfig.get()
    app_config    = _load_config()
    cover_template = ''
    if os.path.exists(COVER_LETTER_PATH):
        with open(COVER_LETTER_PATH) as f:
            cover_template = f.read()
    if not cover_template:
        cover_template = DEFAULT_TEMPLATE

    if request.method == 'POST':
        search_config.keywords                 = request.form.get('keywords', '')
        search_config.location                 = request.form.get('location', '')
        search_config.remote_only              = 'remote_only' in request.form
        search_config.blacklisted_companies    = request.form.get('blacklisted_companies', '')
        search_config.platforms                = request.form.get('platforms', '')
        search_config.auto_approve             = 'auto_approve' in request.form
        search_config.max_applications_per_run = int(request.form.get('max_applications_per_run', 10))
        db.session.commit()

        with open(COVER_LETTER_PATH, 'w') as f:
            f.write(request.form.get('cover_template', ''))

        app_config['profile'] = {
            'full_name':        request.form.get('full_name', ''),
            'first_name':       request.form.get('first_name', ''),
            'last_name':        request.form.get('last_name', ''),
            'email':            request.form.get('email', ''),
            'phone':            request.form.get('phone', ''),
            'linkedin':         request.form.get('linkedin', ''),
            'website':          request.form.get('website', ''),
            'field':            request.form.get('field', ''),
            'years_experience': request.form.get('years_experience', ''),
            'custom_paragraph': request.form.get('custom_paragraph', ''),
        }
        app_config['browser_profile'] = request.form.get('browser_profile', '/tmp/job_browser')
        app_config['dry_run']         = 'dry_run' in request.form

        gh    = request.form.get('greenhouse_companies', '')
        lever = request.form.get('lever_companies', '')
        app_config['greenhouse_companies'] = [c.strip() for c in gh.splitlines()    if c.strip()]
        app_config['lever_companies']      = [c.strip() for c in lever.splitlines() if c.strip()]

        if 'resume' in request.files:
            file = request.files['resume']
            if file and file.filename:
                filename = secure_filename(file.filename)
                path = os.path.join(UPLOAD_FOLDER, filename)
                file.save(path)
                app_config['resume_path'] = path

        _save_config(app_config)
        flash('Settings saved.', 'success')
        return redirect(url_for('settings'))

    return render_template('settings.html',
                           search_config=search_config,
                           app_config=app_config,
                           cover_template=cover_template)


# ── Runner API ────────────────────────────────────────────────────────────────

@app.route('/api/scrape', methods=['POST'])
@login_required
def api_scrape():
    if runner.get_status()['running']:
        return jsonify({'error': 'Already running'}), 409
    runner.scrape_jobs(app)
    return jsonify({'ok': True})


@app.route('/api/apply', methods=['POST'])
@login_required
def api_apply():
    if runner.get_status()['running']:
        return jsonify({'error': 'Already running'}), 409
    runner.apply_approved(app)
    return jsonify({'ok': True})


@app.route('/api/status')
@login_required
def api_status():
    return jsonify(runner.get_status())


if __name__ == '__main__':
    cfg = _load_config()
    if not cfg.get('password_hash'):
        print('\n  No password set. Run this first:')
        print('  python set_password.py\n')
    app.run(debug=False, port=5055, use_reloader=False)
