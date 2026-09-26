import json
from datetime import date, datetime, timedelta
from flask import Blueprint, render_template, request, redirect, url_for, session, flash, abort

from database import query_db, mutate_db
from helpers import (
    get_current_user, get_employee, ROLE_PERMISSIONS,
    get_unread_notifications, add_notification,
)
from models import validate_csrf, audit, check_password, hash_password
from decorators import login_required, role_required

general_bp = Blueprint('general', __name__)


def _require_user(user, flash_msg=None):
    """If user is None (no employee record linked to session), show an error and redirect."""
    if user is None:
        msg = flash_msg or (
            'Your account is not linked to an employee record. '
            'Please contact HR or your system administrator.'
        )
        flash(msg, 'error')
        session.clear()
        return redirect(url_for('auth.login'))
    return None


@general_bp.route('/')
def index():
    if not session.get('logged_in'):
        return redirect(url_for('auth.login'))
    return redirect(url_for('general.dashboard'))


@general_bp.route('/dashboard')
@login_required
def dashboard():
    role, user = get_current_user()
    redir = _require_user(user)
    if redir:
        return redir
    pending = query_db("SELECT * FROM leave_requests WHERE status='pending'")
    balances = {r['leave_key']: r['days']
                for r in query_db("SELECT * FROM leave_balances WHERE employee_id=?", (user['id'],))}
    today = date.today().isoformat()
    on_leave_today_raw = query_db(
        "SELECT * FROM leave_requests WHERE status='approved' AND from_date<=? AND to_date>=?",
        (today, today))
    on_leave_today = [
        {**dict(r), 'employee': get_employee(r['employee_id'])}
        for r in on_leave_today_raw
    ]
    stats = {
        'pending_count': len(pending),
        'on_leave': len(on_leave_today),
        'total_staff': query_db("SELECT COUNT(*) as c FROM employees", one=True)['c'],
        'balances': balances,
    }
    recent = pending[:3]
    enriched = [{**dict(r), 'employee': get_employee(r['employee_id'])} for r in recent]
    all_balances = {}
    for emp_row in query_db("SELECT id FROM employees"):
        eid = emp_row['id']
        all_balances[eid] = {r['leave_key']: r['days']
                             for r in query_db("SELECT * FROM leave_balances WHERE employee_id=?", (eid,))}
    return render_template('dashboard.html',
        role=role, user=user, roles=ROLE_PERMISSIONS,
        stats=stats, recent_requests=enriched,
        on_leave_today=on_leave_today,
        leave_balances=all_balances,
        notifications=get_unread_notifications(user['id']))


@general_bp.route('/calendar')
@login_required
def calendar():
    role, user = get_current_user()
    redir = _require_user(user)
    if redir:
        return redir
    active = query_db("SELECT * FROM leave_requests WHERE status IN ('approved','pending')")
    enriched = [{**dict(r), 'employee': get_employee(r['employee_id'])} for r in active]
    for r in enriched:
        r['from'] = r.pop('from_date', r.get('from'))
        r['to']   = r.pop('to_date',   r.get('to'))
        r['type'] = r.pop('leave_type', r.get('type'))
    holidays = [dict(h) for h in query_db("SELECT * FROM public_holidays ORDER BY date")]
    return render_template('calendar.html',
        role=role, user=user, roles=ROLE_PERMISSIONS,
        leave_events=json.dumps(enriched),
        holidays=json.dumps(holidays),
        notifications=get_unread_notifications(user['id']))


@general_bp.route('/profile')
@login_required
def profile():
    role, user = get_current_user()
    redir = _require_user(user)
    if redir:
        return redir
    balances = {r['leave_key']: r['days']
                for r in query_db("SELECT * FROM leave_balances WHERE employee_id=?", (user['id'],))}
    return render_template('profile.html',
        role=role, user=user, roles=ROLE_PERMISSIONS,
        balances=balances,
        notifications=get_unread_notifications(user['id']))


@general_bp.route('/profile/change-password', methods=['POST'])
@login_required
def change_password():
    validate_csrf()
    role, user = get_current_user()
    redir = _require_user(user)
    if redir:
        return redir
    username = session.get('username')
    current  = request.form.get('current_password', '')
    new_pw   = request.form.get('new_password', '')
    confirm  = request.form.get('confirm_password', '')

    user_rec = query_db("SELECT * FROM users WHERE username=?", (username,), one=True)
    if not user_rec or not check_password(user_rec['password_hash'], current):
        flash('Current password is incorrect.', 'error')
        return redirect(url_for('general.profile'))

    if len(new_pw) < 8:
        flash('New password must be at least 8 characters.', 'error')
        return redirect(url_for('general.profile'))

    if new_pw != confirm:
        flash('New passwords do not match.', 'error')
        return redirect(url_for('general.profile'))

    mutate_db("UPDATE users SET password_hash=? WHERE username=?",
              (hash_password(new_pw), username))
    audit(username, user['name'], 'change_password', 'Self-service password change')
    flash('Password updated successfully.', 'success')
    return redirect(url_for('general.profile'))



@general_bp.route('/profile/update', methods=['POST'])
@login_required
def update_profile():
    validate_csrf()
    role, user = get_current_user()
    redir = _require_user(user)
    if redir:
        return redir

    email = request.form.get('email', '').strip().lower()
    phone = request.form.get('phone', '').strip()

    if not email:
        flash('Email address cannot be empty.', 'error')
        return redirect(url_for('general.profile'))

    # Basic email validation
    if '@' not in email or '.' not in email.split('@')[-1]:
        flash('Please enter a valid email address.', 'error')
        return redirect(url_for('general.profile'))

    try:
        if phone:
            mutate_db(
                "UPDATE employees SET email=%s, phone=%s WHERE id=%s",
                (email, phone, user['id'])
            )
        else:
            mutate_db(
                "UPDATE employees SET email=%s WHERE id=%s",
                (email, user['id'])
            )
        audit(session.get('username'), user['name'], 'update_profile',
              f"Updated contact details: email={email}")
        flash('Contact details updated successfully.', 'success')
    except Exception as e:
        import logging
        logging.getLogger(__name__).exception("update_profile failed: %s", e)
        flash('Could not save changes. Please try again.', 'error')

    return redirect(url_for('general.profile'))

@general_bp.route('/notifications')
@login_required
def notifications_view():
    role, user = get_current_user()
    redir = _require_user(user)
    if redir:
        return redir
    emp_notifs = [dict(r) for r in query_db(
        "SELECT * FROM notifications WHERE employee_id=? ORDER BY id DESC", (user['id'],))]
    return render_template('notifications.html',
        role=role, user=user, roles=ROLE_PERMISSIONS,
        notifications_all=emp_notifs,
        notifications=get_unread_notifications(user['id']))


@general_bp.route('/notifications/read/<int:notif_id>', methods=['POST'])
@login_required
def mark_notification_read(notif_id):
    validate_csrf()
    role, user = get_current_user()
    redir = _require_user(user)
    if redir:
        return redir
    notif = query_db("SELECT * FROM notifications WHERE id=?", (notif_id,), one=True)
    if notif and notif['employee_id'] == user['id']:
        mutate_db("UPDATE notifications SET is_read=1 WHERE id=?", (notif_id,))
    return redirect(url_for('general.notifications_view'))
