import os
import importlib
import warnings
from datetime import timedelta
from flask import Flask, render_template, session

from database import close_db
from models import init_db, generate_csrf_token
from helpers import get_current_user, ROLE_PERMISSIONS, get_unread_notifications

from blueprints.auth import auth_bp
from blueprints.general import general_bp
from blueprints.leave import leave_bp
from blueprints.admin import admin_bp


# ── App setup ─────────────────────────────────────────────────────────────────

app = Flask(__name__)

_secret = os.environ.get('SECRET_KEY')
if not _secret:
    warnings.warn(
        "SECRET_KEY env var not set — using insecure dev key. Set it before deploying.",
        stacklevel=1,
    )
    _secret = 'dev-only-insecure-key-change-before-deploy'
app.secret_key = _secret

app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(minutes=30)
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
# Uncomment in production (requires HTTPS):
# app.config['SESSION_COOKIE_SECURE'] = True

# ── Rate limiting ──────────────────────────────────────────────────────────────

def get_remote_address():
    from flask import request
    if request.environ.get('HTTP_X_FORWARDED_FOR'):
        return request.environ.get('HTTP_X_FORWARDED_FOR').split(',')[0]
    return request.remote_addr

try:
    _Limiter = importlib.import_module('flask_limiter').Limiter
except ImportError:
    _Limiter = None

if _Limiter is None:
    class _NoopLimiter:
        def limit(self, *args, **kwargs):
            def decorator(f):
                return f
            return decorator
    limiter = _NoopLimiter()
else:
    limiter = _Limiter(
        get_remote_address,
        app=app,
        default_limits=[],
        storage_uri='memory://',
    )

# ── Teardown & globals ────────────────────────────────────────────────────────

app.teardown_appcontext(close_db)
app.jinja_env.globals['csrf_token'] = generate_csrf_token

# ── Blueprints ────────────────────────────────────────────────────────────────

app.register_blueprint(auth_bp)
app.register_blueprint(general_bp)
app.register_blueprint(leave_bp)
app.register_blueprint(admin_bp)

# ── Error handlers ────────────────────────────────────────────────────────────

@app.errorhandler(403)
def forbidden(e):
    role, user = get_current_user()
    return render_template('403.html', role=role, user=user,
                           roles=ROLE_PERMISSIONS, notifications=[]), 403


@app.errorhandler(500)
def internal_error(e):
    """Return a clean 500 page instead of a raw traceback.
    Also attempts to roll back any broken DB transaction so subsequent
    requests don't inherit a bad connection state.
    """
    import logging
    logging.getLogger(__name__).exception("Unhandled 500: %s", e)
    try:
        from flask import g
        db = getattr(g, '_database', None)
        if db and not db.closed:
            db.rollback()
    except Exception:
        pass
    try:
        role, user = get_current_user()
    except Exception:
        role, user = None, None
    return render_template('500.html', role=role, user=user,
                           roles=ROLE_PERMISSIONS, notifications=[]), 500

@app.errorhandler(429)
def ratelimit_handler(e):
    return render_template('login.html',
        error="Too many login attempts. Please wait a moment and try again.",
        role=None, user=None), 429

# ── Init DB & scheduled tasks ─────────────────────────────────────────────────

init_db(app)

with app.app_context():
    from tasks import run_monthly_carry_forward, run_year_end_carryover
    run_monthly_carry_forward()
    run_year_end_carryover()

# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == '__main__':
    app.run(debug=False)
