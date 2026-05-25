"""
Authentication: session-based single-user login.
Password is stored as a werkzeug pbkdf2 hash in config.yaml.
Rate limiter: max 5 failed attempts per IP per 60 seconds (in-memory).
CSRF: per-session token validated on all state-changing requests.
"""
import os
import secrets
import time
from collections import defaultdict
from functools import wraps

from flask import session, redirect, url_for, request, abort
from werkzeug.security import generate_password_hash, check_password_hash

# ── Rate limiter state ────────────────────────────────────────────────────────
_attempts: dict[str, list[float]] = defaultdict(list)
_WINDOW   = 60    # seconds
_MAX_HITS = 5


def _client_ip() -> str:
    return request.headers.get('X-Forwarded-For', request.remote_addr or '').split(',')[0].strip()


def rate_limited() -> bool:
    """Return True (and record attempt) if this IP has exceeded the limit."""
    ip  = _client_ip()
    now = time.monotonic()
    _attempts[ip] = [t for t in _attempts[ip] if now - t < _WINDOW]
    _attempts[ip].append(now)
    return len(_attempts[ip]) > _MAX_HITS


def clear_rate_limit():
    """Call on successful login to reset the counter for this IP."""
    _attempts.pop(_client_ip(), None)


# ── CSRF ──────────────────────────────────────────────────────────────────────
def csrf_token() -> str:
    if 'csrf_token' not in session:
        session['csrf_token'] = secrets.token_hex(32)
    return session['csrf_token']


def validate_csrf():
    """Abort 403 if the submitted CSRF token doesn't match the session token."""
    submitted = request.form.get('csrf_token', '')
    expected  = session.get('csrf_token', '')
    if not submitted or not secrets.compare_digest(submitted, expected):
        abort(403)


# ── Password helpers ──────────────────────────────────────────────────────────
def hash_password(plaintext: str) -> str:
    return generate_password_hash(plaintext, method='pbkdf2:sha256', salt_length=16)


def verify_password(plaintext: str, hashed: str) -> bool:
    return check_password_hash(hashed, plaintext)


# ── Session auth ──────────────────────────────────────────────────────────────
def is_logged_in() -> bool:
    return session.get('authenticated') is True


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not is_logged_in():
            return redirect(url_for('login', next=request.path))
        return f(*args, **kwargs)
    return decorated
