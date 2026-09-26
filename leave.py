import os
import uuid
from datetime import date, timedelta
from flask import Blueprint, render_template, request, redirect, url_for, session, abort, current_app

from database import query_db, mutate_db
from helpers import (
    get_current_user, get_employee, ROLE_PERMISSIONS, LEAVE_ENTITLEMENTS,
    get_unread_notifications, add_notification,
    count_working_days, has_overlap, deduct_balance, restore_balance,
)
from models import validate_csrf, audit
from decorators import login_required, role_required

leave_bp = Blueprint('leave', __name__)

LEAVE_POLICIES = {
    "Annual Leave": {
        "icon": "ti-sun", "color": "amber",
        "rules": [
            "Must be applied at least 3 working days in advance.",
            "Can be split into multiple periods within the year.",
            "A maximum of 10 unused days carry over to the next year; any excess is forfeited.",
        ]
    },
    "Sick Leave": {
        "icon": "ti-stethoscope", "color": "red",
        "rules": [
            "A medical certificate is required for absences exceeding 3 consecutive days.",
            "Notify your manager on the first day of absence.",
            "Sick leave cannot be taken immediately before or after annual leave without a certificate.",
        ]
    },
    "Compassionate Leave": {
        "icon": "ti-heart", "color": "purple",
        "rules": [
            "Capped at 3 days per incident.",
            "Applicable for immediate family bereavement or serious illness.",
            "Supporting documentation (e.g. death certificate) may be requested.",
        ]
    },
    "Emergency Leave": {
        "icon": "ti-alert-triangle", "color": "red",
        "rules": [
            "Limited to 3 days per year.",
            "For unforeseen personal emergencies (e.g. sudden illness of a dependent, urgent home incident).",
            "Must be reported to your manager on the same day; supporting documentation may be requested.",
        ]
    },
    "Maternity Leave": {
        "icon": "ti-baby-carriage", "color": "purple",
        "rules": [
            "90 days fully paid maternity leave.",
            "Should be applied at least 4 weeks before expected delivery date.",
            "A medical certificate confirming the due date is required.",
        ]
    },
    "Paternity Leave": {
        "icon": "ti-man", "color": "blue",
        "rules": [
            "10 days paid paternity leave.",
            "Must be taken within 3 months of the child's birth.",
            "Birth certificate or hospital documentation required upon return.",
        ]
    },
}

UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), "..", "static", "uploads")
ALLOWED_SICK_EXTS = {".pdf", ".jpg", ".jpeg", ".png"}


def _require_user(user):
    if user is None:
        session.clear()
        return redirect(url_for('auth.login'))
    return None


@leave_bp.route('/apply', methods=['GET', 'POST'])
@login_required
@role_required('employee', 'manager', 'hr')
def apply_leave():
    role, user = get_current_user()
    redir = _require_user(user)
    if redir:
        return redir
    form_error = None

    if request.method == 'POST':
        validate_csrf()
        from_date  = request.form.get('from_date', '')
        to_date    = request.form.get('to_date', '')
        leave_type = request.form.get('leave_type', '')
        reason     = request.form.get('reason', '')

        if not from_date or not to_date or not leave_type:
            form_error = "Please fill in all required fields."
        else:
            from datetime import datetime
            d1 = datetime.strptime(from_date, '%Y-%m-%d').date()
            d2 = datetime.strptime(to_date,   '%Y-%m-%d').date()
            if d2 < d1:
                form_error = "End date cannot be before start date."
            elif d1 < date.today():
                form_error = "Start date cannot be in the past."
            elif has_overlap(user['id'], from_date, to_date):
                form_error = "You already have a pending or approved leave request overlapping these dates."
            else:
                days = count_working_days(from_date, to_date)
                if days == 0:
                    form_error = "Selected range contains no working days."
                else:
                    info = LEAVE_ENTITLEMENTS.get(leave_type, {})
                    required_gender = info.get("gender") if info else None
                    if required_gender and user.get("gender") != required_gender:
                        gender_label = "female employees" if required_gender == "female" else "male employees"
                        form_error = f"{leave_type} is only available to {gender_label}."
                    else:
                        bal_key = info.get("key") if info else None
                        if bal_key:
                            bal_row = query_db(
                                "SELECT days FROM leave_balances WHERE employee_id=? AND leave_key=?",
                                (user['id'], bal_key), one=True)
                            remaining = bal_row['days'] if bal_row else 0
                            if days > remaining:
                                form_error = (f"Insufficient balance. You have {remaining} "
                                              f"{leave_type} day(s) but requested {days}.")

        sick_filename = None
        if not form_error:
            uploaded_file = request.files.get('sick_form')
            if uploaded_file and uploaded_file.filename:
                ext = os.path.splitext(uploaded_file.filename)[1].lower()
                if ext in ALLOWED_SICK_EXTS:
                    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
                    sick_filename = str(uuid.uuid4()) + ext
                    uploaded_file.save(os.path.join(UPLOAD_FOLDER, sick_filename))
            mutate_db(
                "INSERT INTO leave_requests (employee_id,leave_type,from_date,to_date,days,"
                "status,reason,applied_date,sick_form_filename) VALUES (?,?,?,?,?,?,?,?,?)",
                (user['id'], leave_type, from_date, to_date, days,
                 'pending', reason, date.today().isoformat(), sick_filename))
            mgr_id = user.get('manager_id')
            if mgr_id:
                add_notification(mgr_id,
                    f"New {leave_type} request from {user['name']} ({from_date} → {to_date}, {days} days)",
                    "info")
            return redirect(url_for('leave.my_leave', success='1'))

    balances = {r['leave_key']: r['days']
                for r in query_db("SELECT * FROM leave_balances WHERE employee_id=?", (user['id'],))}

    team_ids_rows = query_db(
        "SELECT id FROM employees WHERE manager_id=? AND id!=?",
        (user.get('manager_id') or -1, user['id']))
    peer_ids = [r['id'] for r in team_ids_rows]
    if user.get('manager_id'):
        same_mgr_rows = query_db(
            "SELECT id FROM employees WHERE manager_id=? AND id!=?",
            (user['manager_id'], user['id']))
        peer_ids += [r['id'] for r in same_mgr_rows]
    peer_ids = list(set(peer_ids)) or []

    team_leaves = []
    if peer_ids:
        placeholders = ','.join('?' * len(peer_ids))
        team_leave_rows = query_db(
            f"""SELECT lr.*, e.name as emp_name, e.initials, e.dept
                FROM leave_requests lr
                JOIN employees e ON e.id = lr.employee_id
                WHERE lr.employee_id IN ({placeholders})
                  AND lr.status IN ('pending','approved')
                  AND lr.to_date >= ?
                ORDER BY lr.from_date""",
            peer_ids + [date.today().isoformat()])
        team_leaves = [dict(r) for r in team_leave_rows]

    upcoming_holidays = [dict(r) for r in query_db(
        "SELECT * FROM public_holidays WHERE date >= ? AND date <= ? ORDER BY date",
        (date.today().isoformat(),
         (date.today() + timedelta(days=90)).isoformat()))]

    return render_template('apply.html', role=role, user=user, roles=ROLE_PERMISSIONS,
                           form_error=form_error,
                           notifications=get_unread_notifications(user['id']),
                           entitlements=LEAVE_ENTITLEMENTS,
                           balances=balances,
                           team_leaves=team_leaves,
                           upcoming_holidays=upcoming_holidays,
                           leave_policies=LEAVE_POLICIES)


@leave_bp.route('/my-leave')
@login_required
@role_required('employee', 'manager', 'hr')
def my_leave():
    role, user = get_current_user()
    redir = _require_user(user)
    if redir:
        return redir
    success = request.args.get('success')
    my_requests = [dict(r) for r in query_db(
        "SELECT * FROM leave_requests WHERE employee_id=? ORDER BY applied_date DESC", (user['id'],))]
    balances = {r['leave_key']: r['days']
                for r in query_db("SELECT * FROM leave_balances WHERE employee_id=?", (user['id'],))}
    return render_template('my_leave.html',
        role=role, user=user, roles=ROLE_PERMISSIONS,
        requests=my_requests, balances=balances, success=success,
        notifications=get_unread_notifications(user['id']))


@leave_bp.route('/cancel/<int:req_id>', methods=['POST'])
@login_required
@role_required('employee', 'manager', 'hr')
def cancel_leave(req_id):
    validate_csrf()
    role, user = get_current_user()
    redir = _require_user(user)
    if redir:
        return redir
    req = query_db("SELECT * FROM leave_requests WHERE id=?", (req_id,), one=True)
    if not req or req['employee_id'] != user['id']:
        abort(403)
    if req['status'] not in ('pending', 'approved'):
        abort(400)
    if req['status'] == 'approved':
        restore_balance(user['id'], req['leave_type'], req['days'])
    mutate_db("UPDATE leave_requests SET status='cancelled' WHERE id=?", (req_id,))
    add_notification(user['id'],
        f"Your {req['leave_type']} ({req['from_date']} → {req['to_date']}) has been cancelled.",
        "warning")
    audit(session.get('username', '?'), user['name'],
          'cancel_leave',
          f"#{req_id} {req['leave_type']} ({req['from_date']} → {req['to_date']})")
    return redirect(url_for('leave.my_leave'))


@leave_bp.route('/approvals')
@login_required
@role_required('manager', 'hr')
def approvals():
    role, user = get_current_user()
    redir = _require_user(user)
    if redir:
        return redir
    status_filter = request.args.get('status', 'all')
    type_filter   = request.args.get('type', 'all')
    sql = "SELECT * FROM leave_requests WHERE 1=1"
    args = []
    if status_filter != 'all':
        sql += " AND status=?"; args.append(status_filter)
    if type_filter != 'all':
        sql += " AND leave_type=?"; args.append(type_filter)
    sql += " ORDER BY applied_date DESC"
    rows = query_db(sql, args)
    enriched = [{**dict(r), 'employee': get_employee(r['employee_id'])} for r in rows]
    return render_template('approvals.html',
        role=role, user=user, roles=ROLE_PERMISSIONS,
        requests=enriched, status_filter=status_filter, type_filter=type_filter,
        notifications=get_unread_notifications(user['id']))


@leave_bp.route('/approve/<int:req_id>', methods=['POST'])
@login_required
@role_required('manager', 'hr')
def approve_request(req_id):
    validate_csrf()
    req = query_db("SELECT * FROM leave_requests WHERE id=? AND status='pending'",
                   (req_id,), one=True)
    if req:
        role, actor = get_current_user()
        mutate_db("UPDATE leave_requests SET status='approved' WHERE id=?", (req_id,))
        deduct_balance(req['employee_id'], req['leave_type'], req['days'])
        add_notification(req['employee_id'],
            f"Your {req['leave_type']} ({req['from_date']} → {req['to_date']}, {req['days']} days) has been approved. ✓",
            "success")
        emp = get_employee(req['employee_id'])
        audit(session.get('username', '?'), actor['name'] if actor else '?',
              'approve_leave',
              f"#{req_id} {req['leave_type']} for {emp['name'] if emp else req['employee_id']} "
              f"({req['from_date']} → {req['to_date']}, {req['days']} days)")
    return redirect(url_for('leave.approvals'))


@leave_bp.route('/bulk-approve', methods=['POST'])
@login_required
@role_required('manager', 'hr')
def bulk_approve():
    validate_csrf()
    req_ids = request.form.getlist('req_ids')
    role, actor = get_current_user()
    for rid in req_ids:
        try:
            rid = int(rid)
        except (ValueError, TypeError):
            continue
        req = query_db("SELECT * FROM leave_requests WHERE id=? AND status='pending'",
                       (rid,), one=True)
        if req:
            mutate_db("UPDATE leave_requests SET status='approved' WHERE id=?", (rid,))
            deduct_balance(req['employee_id'], req['leave_type'], req['days'])
            add_notification(req['employee_id'],
                f"Your {req['leave_type']} ({req['from_date']} → {req['to_date']}, {req['days']} days) has been approved. ✓",
                "success")
            emp = get_employee(req['employee_id'])
            audit(session.get('username', '?'), actor['name'] if actor else '?',
                  'approve_leave',
                  f"#{rid} {req['leave_type']} for {emp['name'] if emp else req['employee_id']} "
                  f"({req['from_date']} → {req['to_date']}, {req['days']} days) [bulk]")
    return redirect(url_for('leave.approvals'))


@leave_bp.route('/bulk-reject', methods=['POST'])
@login_required
@role_required('manager', 'hr')
def bulk_reject():
    validate_csrf()
    req_ids = request.form.getlist('req_ids')
    reason = request.form.get('bulk_rejection_reason', '').strip()
    role, actor = get_current_user()
    for rid in req_ids:
        try:
            rid = int(rid)
        except (ValueError, TypeError):
            continue
        req = query_db("SELECT * FROM leave_requests WHERE id=? AND status='pending'",
                       (rid,), one=True)
        if req:
            mutate_db(
                "UPDATE leave_requests SET status='rejected', rejection_reason=? WHERE id=?",
                (reason or None, rid))
            msg = f"Your {req['leave_type']} ({req['from_date']} → {req['to_date']}) was rejected."
            if reason:
                msg += f" Reason: {reason}"
            add_notification(req['employee_id'], msg, "warning")
            emp = get_employee(req['employee_id'])
            audit(session.get('username', '?'), actor['name'] if actor else '?',
                  'reject_leave',
                  f"#{rid} {req['leave_type']} for {emp['name'] if emp else req['employee_id']} "
                  f"({req['from_date']} → {req['to_date']}) [bulk]" + (f" — {reason}" if reason else ""))
    return redirect(url_for('leave.approvals'))


@leave_bp.route('/reject/<int:req_id>', methods=['POST'])
@login_required
@role_required('manager', 'hr')
def reject_request(req_id):
    validate_csrf()
    req = query_db("SELECT * FROM leave_requests WHERE id=? AND status='pending'",
                   (req_id,), one=True)
    reason = request.form.get('rejection_reason', '').strip()
    if req:
        role, actor = get_current_user()
        mutate_db(
            "UPDATE leave_requests SET status='rejected', rejection_reason=? WHERE id=?",
            (reason or None, req_id))
        msg = f"Your {req['leave_type']} ({req['from_date']} → {req['to_date']}) was rejected."
        if reason:
            msg += f" Reason: {reason}"
        add_notification(req['employee_id'], msg, "warning")
        emp = get_employee(req['employee_id'])
        audit(session.get('username', '?'), actor['name'] if actor else '?',
              'reject_leave',
              f"#{req_id} {req['leave_type']} for {emp['name'] if emp else req['employee_id']} "
              f"({req['from_date']} → {req['to_date']})" + (f" — {reason}" if reason else ""))
    return redirect(url_for('leave.approvals'))
