import importlib
from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, session

from models import (
    check_password, generate_csrf_token, validate_csrf,
    is_locked, record_fail, clear_lockout,
    MAX_FAIL_ATTEMPTS, LOCKOUT_MINUTES, audit,
)
from database import query_db
from helpers import get_current_user, ROLE_PERMISSIONS, get_unread_notifications, add_notification

auth_bp = Blueprint('auth', __name__)


def get_remote_address():
    if request.environ.get('HTTP_X_FORWARDED_FOR'):
        return request.environ.get('HTTP_X_FORWARDED_FOR').split(',')[0]
    return request.remote_addr


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if session.get('logged_in'):
        return redirect(url_for('general.dashboard'))
    error = None
    lockout_seconds = 0
    if request.method == 'POST':
        validate_csrf()
        username = request.form.get('username', '').strip().lower()
        password = request.form.get('password', '')

        locked, lockout_seconds = is_locked(username)
        if locked:
            mins = lockout_seconds // 60
            secs = lockout_seconds % 60
            error = (f"Account temporarily locked due to too many failed attempts. "
                     f"Try again in {mins}m {secs}s.")
            audit(username, username, 'login_blocked',
                  f"Login attempt while locked. IP: {get_remote_address()}")
            return render_template('login.html', error=error, lockout_seconds=lockout_seconds)

        user_rec = query_db("SELECT * FROM users WHERE username=?", (username,), one=True)
        if user_rec and check_password(user_rec['password_hash'], password):
            had_lockout = query_db(
                "SELECT 1 FROM account_lockouts WHERE username=?", (username,), one=True)
            clear_lockout(username)
            if had_lockout:
                audit(username, username, 'account_unlocked',
                      f"Lockout cleared on successful login. IP: {get_remote_address()}")
            # Look up the employee record linked to this username
            emp_rec = query_db("SELECT id FROM employees WHERE username=?", (username,), one=True)
            session.clear()
            session.permanent = True
            session['logged_in'] = True
            session['username'] = username
            session['role'] = user_rec['role']
            session['last_active'] = datetime.utcnow().isoformat()
            if emp_rec:
                session['employee_id'] = emp_rec['id']
            else:
                # No employee row linked to this user account — profile & most routes
                # will redirect to login. Log it so an admin can fix the data.
                audit(username, username, 'login_no_employee',
                      f"User authenticated but no employee record found for '{username}'. "
                      "Contact HR to link this user account to an employee record.")
            generate_csrf_token()
            audit(username, username, 'login', f"IP: {get_remote_address()}")
            return redirect(request.args.get('next') or url_for('general.dashboard'))

        audit(username, username, 'login_failed',
              f"Invalid credentials. IP: {get_remote_address()}")
        fail_count = record_fail(username)
        remaining = max(0, MAX_FAIL_ATTEMPTS - fail_count)
        if fail_count >= MAX_FAIL_ATTEMPTS:
            audit(username, username, 'account_locked',
                  f"Account locked after {fail_count} failed attempts. IP: {get_remote_address()}")
            # Notify all HR users
            hr_users = query_db(
                "SELECT e.id FROM employees e JOIN users u ON u.username = e.username WHERE u.role = 'hr'"
            )
            for hr in hr_users:
                add_notification(
                    hr['id'],
                    f"\u26a0\ufe0f Account locked: \'{username}\' was locked after {fail_count} failed login attempts. IP: {get_remote_address()}",
                    "warning"
                )
            error = f"Too many failed attempts — account locked for {LOCKOUT_MINUTES} minutes."
        elif remaining <= 2:
            error = f"Invalid username or password. {remaining} attempt(s) remaining before lockout."
        else:
            error = 'Invalid username or password.'
    return render_template('login.html', error=error, lockout_seconds=lockout_seconds)


@auth_bp.route('/logout')
def logout():
    username = session.get('username', 'unknown')
    audit(username, username, 'logout', f"IP: {get_remote_address()}")
    session.clear()
    return redirect(url_for('auth.login'))
