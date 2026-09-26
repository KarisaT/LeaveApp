from functools import wraps
from datetime import datetime, timedelta
from flask import session, redirect, url_for, request, abort


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('auth.login', next=request.path))
        last = session.get('last_active')
        if last:
            idle = datetime.utcnow() - datetime.fromisoformat(last)
            if idle > timedelta(minutes=30):
                session.clear()
                return redirect(url_for('auth.login'))
        session['last_active'] = datetime.utcnow().isoformat()
        return f(*args, **kwargs)
    return decorated


def role_required(*allowed_roles):
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if session.get('role') not in allowed_roles:
                abort(403)
            return f(*args, **kwargs)
        return decorated
    return decorator
