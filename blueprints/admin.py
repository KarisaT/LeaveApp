from flask import Blueprint, render_template, request, redirect, url_for, abort, flash, session

from database import query_db, mutate_db, mutate_db_returning
from helpers import (
    get_current_user, get_employee, ROLE_PERMISSIONS, LEAVE_ENTITLEMENTS,
    get_unread_notifications, add_notification,
)
from models import validate_csrf, hash_password
from decorators import login_required, role_required

admin_bp = Blueprint('admin', __name__)


def _require_user(user):
    if user is None:
        session.clear()
        return redirect(url_for('auth.login'))
    return None


@admin_bp.route('/employees')
@login_required
@role_required('manager', 'hr', 'payroll')
def employees_view():
    role, user = get_current_user()
    redir = _require_user(user)
    if redir:
        return redir
    emps = [dict(e) for e in query_db("SELECT * FROM employees ORDER BY name")]
    for emp in emps:
        emp['balances'] = {r['leave_key']: r['days']
                           for r in query_db("SELECT * FROM leave_balances WHERE employee_id=?",
                                             (emp['id'],))}
    managers = [dict(e) for e in query_db("SELECT id, name FROM employees ORDER BY name")]
    return render_template('employees.html',
        role=role, user=user, roles=ROLE_PERMISSIONS, employees=emps,
        managers=managers,
        notifications=get_unread_notifications(user['id']))


@admin_bp.route('/employees/add', methods=['POST'])
@login_required
@role_required('hr')
def add_employee():
    validate_csrf()
    name       = request.form.get('name', '').strip()
    initials   = request.form.get('initials', '').strip().upper()
    dept       = request.form.get('dept', '').strip()
    role_title = request.form.get('role_title', '').strip()
    email      = request.form.get('email', '').strip().lower()
    start_date = request.form.get('start_date', '').strip()
    manager_id = request.form.get('manager_id') or None
    username   = request.form.get('username', '').strip().lower()
    password   = request.form.get('password', '').strip()
    user_role  = request.form.get('user_role', 'employee')

    if not all([name, initials, dept, role_title, email, start_date, username, password]):
        flash('All fields are required.', 'error')
        return redirect(url_for('admin.employees_view'))

    if query_db("SELECT 1 FROM users WHERE username=?", (username,), one=True):
        flash(f'Username "{username}" is already taken.', 'error')
        return redirect(url_for('admin.employees_view'))

    gender = request.form.get('gender', 'male')
    if gender not in ('male', 'female'):
        gender = 'male'

    mutate_db("INSERT INTO users (username,password_hash,role) VALUES (?,?,?)",
              (username, hash_password(password), user_role))

    emp_id = mutate_db_returning(
        "INSERT INTO employees (name,initials,dept,role_title,email,start_date,manager_id,gender,username) "
        "VALUES (?,?,?,?,?,?,?,?,?) RETURNING id",
        (name, initials, dept, role_title, email, start_date,
         int(manager_id) if manager_id else None, gender, username))

    parental_key  = 'maternity' if gender == 'female' else 'paternity'
    parental_days = 90           if gender == 'female' else 10
    for key, days in [('annual', 21), ('sick', 30), ('emergency', 3),
                      ('compassionate', 3), (parental_key, parental_days)]:
        mutate_db("INSERT INTO leave_balances (employee_id,leave_key,days) VALUES (?,?,?)",
                  (emp_id, key, days))

    add_notification(emp_id,
        f"Welcome to Groot Leave, {name}! Your account has been set up.", "success")

    flash(f'{name} has been added successfully.', 'success')
    return redirect(url_for('admin.employees_view'))


@admin_bp.route('/employees/edit/<int:emp_id>', methods=['POST'])
@login_required
@role_required('hr')
def edit_employee(emp_id):
    validate_csrf()
    name       = request.form.get('name', '').strip()
    initials   = request.form.get('initials', '').strip().upper()
    dept       = request.form.get('dept', '').strip()
    role_title = request.form.get('role_title', '').strip()
    email      = request.form.get('email', '').strip().lower()
    start_date = request.form.get('start_date', '').strip()
    manager_id = request.form.get('manager_id') or None

    if not all([name, initials, dept, role_title, email, start_date]):
        flash('All fields are required.', 'error')
        return redirect(url_for('admin.employees_view'))

    gender = request.form.get('gender', 'male')
    if gender not in ('male', 'female'):
        gender = 'male'
    mutate_db(
        "UPDATE employees SET name=?,initials=?,dept=?,role_title=?,email=?,start_date=?,manager_id=?,gender=? WHERE id=?",
        (name, initials, dept, role_title, email, start_date,
         int(manager_id) if manager_id else None, gender, emp_id))
    flash(f'{name} has been updated successfully.', 'success')
    return redirect(url_for('admin.employees_view'))


@admin_bp.route('/employees/balances/<int:emp_id>', methods=['POST'])
@login_required
@role_required('hr')
def edit_leave_balances(emp_id):
    validate_csrf()
    emp = get_employee(emp_id)
    if not emp:
        abort(404)

    for key in ('annual', 'sick', 'emergency', 'compassionate'):
        val = request.form.get(f'bal_{key}')
        if val is not None:
            try:
                days = max(0, int(val))
                existing = query_db(
                    "SELECT 1 FROM leave_balances WHERE employee_id=? AND leave_key=?",
                    (emp_id, key), one=True)
                if existing:
                    mutate_db(
                        "UPDATE leave_balances SET days=? WHERE employee_id=? AND leave_key=?",
                        (days, emp_id, key))
                else:
                    mutate_db(
                        "INSERT INTO leave_balances (employee_id,leave_key,days) VALUES (?,?,?)",
                        (emp_id, key, days))
            except (ValueError, TypeError):
                pass

    add_notification(emp_id, "Your leave balances have been updated by HR.", "info")
    flash(f'Leave balances updated for {emp["name"]}.', 'success')
    return redirect(url_for('admin.employees_view'))


@admin_bp.route('/holidays', methods=['GET', 'POST'])
@login_required
def holidays_view():
    role, user = get_current_user()
    redir = _require_user(user)
    if redir:
        return redir
    if request.method == 'POST':
        if role != 'hr':
            abort(403)
        validate_csrf()
        mutate_db(
            "INSERT OR IGNORE INTO public_holidays (date,name,type) VALUES (?,?,?)",
            (request.form.get('date'), request.form.get('name'),
             request.form.get('type', 'National Holiday')))
        return redirect(url_for('admin.holidays_view'))
    holidays = [dict(h) for h in query_db("SELECT * FROM public_holidays ORDER BY date")]
    return render_template('holidays.html',
        role=role, user=user, roles=ROLE_PERMISSIONS,
        holidays=holidays,
        notifications=get_unread_notifications(user['id']))


@admin_bp.route('/holidays/delete/<int:hol_id>', methods=['POST'])
@login_required
@role_required('hr')
def delete_holiday(hol_id):
    validate_csrf()
    mutate_db("DELETE FROM public_holidays WHERE id=?", (hol_id,))
    return redirect(url_for('admin.holidays_view'))


@admin_bp.route('/reports')
@login_required
@role_required('hr', 'payroll', 'manager')
def reports():
    role, user = get_current_user()
    redir = _require_user(user)
    if redir:
        return redir
    type_totals, dept_totals = {}, {}
    status_counts = {'approved': 0, 'rejected': 0, 'pending': 0}
    for req in query_db("SELECT * FROM leave_requests"):
        t = req['leave_type']
        type_totals[t] = type_totals.get(t, 0) + req['days']
        sc = req['status']
        if sc in status_counts:
            status_counts[sc] += 1
        emp = get_employee(req['employee_id'])
        if emp:
            dept_totals[emp['dept']] = dept_totals.get(emp['dept'], 0) + req['days']
    total_resolved = status_counts['approved'] + status_counts['rejected']
    approval_rate  = round(status_counts['approved'] / total_resolved * 100) if total_resolved else 0

    sick_rows = query_db(
        """SELECT lr.employee_id, COUNT(*) as occurrences, SUM(lr.days) as total_days
           FROM leave_requests lr
           WHERE lr.leave_type='Sick Leave' AND lr.status='approved'
           GROUP BY lr.employee_id
           ORDER BY occurrences DESC""")
    absenteeism = []
    for row in sick_rows:
        emp = get_employee(row['employee_id'])
        if not emp:
            continue
        occurrences = row['occurrences']
        total_days  = row['total_days'] or 0
        bal_row = query_db(
            "SELECT days FROM leave_balances WHERE employee_id=? AND leave_key='sick'",
            (row['employee_id'],), one=True)
        remaining = bal_row['days'] if bal_row else 0
        sick_max  = 30
        used      = sick_max - remaining
        flagged   = occurrences >= 3 or used > (sick_max * 0.5)
        absenteeism.append({
            'employee': emp,
            'occurrences': occurrences,
            'total_days': total_days,
            'used': used,
            'remaining': remaining,
            'flagged': flagged,
        })

    return render_template('reports.html',
        role=role, user=user, roles=ROLE_PERMISSIONS,
        type_totals=type_totals, dept_totals=dept_totals,
        status_counts=status_counts, approval_rate=approval_rate,
        absenteeism=absenteeism,
        notifications=get_unread_notifications(user['id']))


@admin_bp.route('/audit-log')
@login_required
@role_required('hr')
def audit_log_view():
    role, user = get_current_user()
    redir = _require_user(user)
    if redir:
        return redir
    action_filter = request.args.get('action', 'all')
    actor_filter  = request.args.get('actor', '').strip()
    sql  = "SELECT * FROM audit_log WHERE 1=1"
    args = []
    if action_filter != 'all':
        sql += " AND action=?"; args.append(action_filter)
    if actor_filter:
        sql += " AND actor_username LIKE ?"; args.append(f"%{actor_filter}%")
    sql += " ORDER BY id DESC LIMIT 200"
    logs = [dict(r) for r in query_db(sql, args)]
    action_types = [r['action'] for r in query_db(
        "SELECT DISTINCT action FROM audit_log ORDER BY action")]
    return render_template('audit_log.html',
        role=role, user=user, roles=ROLE_PERMISSIONS,
        logs=logs, action_filter=action_filter, actor_filter=actor_filter,
        action_types=action_types,
        notifications=get_unread_notifications(user['id']))


@admin_bp.route('/parental-leave', methods=['GET'])
@login_required
@role_required('hr', 'manager')
def parental_leave_view():
    from datetime import date
    role, user = get_current_user()
    redir = _require_user(user)
    if redir:
        return redir
    employees = [dict(e) for e in query_db(
        "SELECT id, name, initials, dept, gender FROM employees ORDER BY name")]
    grants = [dict(g) for g in query_db(
        """SELECT plg.*, e.name as emp_name, e.dept
           FROM parental_leave_grants plg
           JOIN employees e ON e.id = plg.employee_id
           ORDER BY plg.granted_at DESC LIMIT 100""")]
    return render_template('parental_leave_admin.html',
        role=role, user=user, roles=ROLE_PERMISSIONS,
        employees=employees, grants=grants,
        today=date.today().isoformat(),
        notifications=get_unread_notifications(user['id']))


@admin_bp.route('/parental-leave/grant', methods=['POST'])
@login_required
@role_required('hr')
def grant_parental_leave():
    from datetime import date, datetime
    from helpers import count_working_days
    from models import audit
    validate_csrf()
    role, user = get_current_user()
    redir = _require_user(user)
    if redir:
        return redir

    emp_id     = request.form.get('employee_id', '').strip()
    leave_type = request.form.get('leave_type', '').strip()
    from_date  = request.form.get('from_date', '').strip()
    to_date    = request.form.get('to_date', '').strip()
    notes      = request.form.get('notes', '').strip()

    if not all([emp_id, leave_type, from_date, to_date]):
        flash('All fields are required.', 'error')
        return redirect(url_for('admin.parental_leave_view'))

    if leave_type not in ('Maternity Leave', 'Paternity Leave'):
        flash('Invalid leave type.', 'error')
        return redirect(url_for('admin.parental_leave_view'))

    emp = query_db("SELECT * FROM employees WHERE id=?", (emp_id,), one=True)
    if not emp:
        flash('Employee not found.', 'error')
        return redirect(url_for('admin.parental_leave_view'))

    if leave_type == 'Maternity Leave' and emp['gender'] != 'female':
        flash('Maternity Leave can only be granted to female employees.', 'error')
        return redirect(url_for('admin.parental_leave_view'))
    if leave_type == 'Paternity Leave' and emp['gender'] != 'male':
        flash('Paternity Leave can only be granted to male employees.', 'error')
        return redirect(url_for('admin.parental_leave_view'))

    try:
        d1 = datetime.strptime(from_date, '%Y-%m-%d').date()
        d2 = datetime.strptime(to_date,   '%Y-%m-%d').date()
    except ValueError:
        flash('Invalid date format.', 'error')
        return redirect(url_for('admin.parental_leave_view'))

    if d2 < d1:
        flash('End date cannot be before start date.', 'error')
        return redirect(url_for('admin.parental_leave_view'))

    days = count_working_days(from_date, to_date)
    if days == 0:
        flash('Selected range contains no working days.', 'error')
        return redirect(url_for('admin.parental_leave_view'))

    mutate_db(
        "INSERT INTO parental_leave_grants "
        "(employee_id, leave_type, from_date, to_date, days, notes, granted_by) "
        "VALUES (?,?,?,?,?,?,?)",
        (emp_id, leave_type, from_date, to_date, days, notes, user['username']))

    mutate_db(
        "INSERT INTO leave_requests "
        "(employee_id, leave_type, from_date, to_date, days, status, reason, applied_date) "
        "VALUES (?,?,?,?,?,'approved',?,?)",
        (emp_id, leave_type, from_date, to_date, days,
         f'Granted by HR: {notes}' if notes else 'Granted by HR',
         date.today().isoformat()))

    add_notification(int(emp_id),
        f"Your {leave_type} ({from_date} → {to_date}, {days} days) has been granted by HR.",
        "success")

    audit(user['username'], user['name'],
          'GRANT_PARENTAL_LEAVE',
          f"{leave_type} for {emp['name']} ({from_date} → {to_date}, {days} days)")

    flash(f"{leave_type} granted successfully for {emp['name']}.", 'success')
    return redirect(url_for('admin.parental_leave_view'))


# ── Delegations ───────────────────────────────────────────────────────────────

@admin_bp.route('/delegations')
@login_required
@role_required('hr', 'manager')
def delegations_view():
    role, user = get_current_user()
    redir = _require_user(user)
    if redir:
        return redir
    from datetime import date
    from models import audit
    rows = query_db("""
        SELECT d.*,
               m.name AS manager_name,
               e.name AS delegate_name
        FROM manager_delegations d
        JOIN employees m ON m.id = d.manager_id
        JOIN employees e ON e.id = d.delegate_id
        ORDER BY d.created_at DESC
    """)
    delegations = [dict(r) for r in rows]
    managers  = [dict(r) for r in query_db(
        "SELECT e.id, e.name FROM employees e JOIN users u ON u.username = e.username WHERE u.role IN ('manager','hr') ORDER BY e.name")]
    employees = [dict(r) for r in query_db(
        "SELECT id, name FROM employees ORDER BY name")]
    return render_template('delegations.html',
        role=role, user=user, roles=ROLE_PERMISSIONS,
        delegations=delegations, managers=managers, employees=employees,
        notifications=get_unread_notifications(user['id']))


@admin_bp.route('/delegations/add', methods=['POST'])
@login_required
@role_required('hr')
def delegations_add():
    validate_csrf()
    role, user = get_current_user()
    redir = _require_user(user)
    if redir:
        return redir
    from datetime import date
    from models import audit
    manager_id  = request.form.get('manager_id', '').strip()
    delegate_id = request.form.get('delegate_id', '').strip()
    from_date   = request.form.get('from_date', '').strip()
    to_date     = request.form.get('to_date', '').strip()
    reason      = request.form.get('reason', '').strip()
    if not manager_id or not delegate_id or not from_date or not to_date:
        flash('All required fields must be filled in.', 'error')
        return redirect(url_for('admin.delegations_view'))
    if to_date < from_date:
        flash('To date cannot be before from date.', 'error')
        return redirect(url_for('admin.delegations_view'))
    mutate_db(
        "INSERT INTO manager_delegations "
        "(manager_id, delegate_id, from_date, to_date, reason, created_by, active) "
        "VALUES (?,?,?,?,?,?,TRUE)",
        (int(manager_id), int(delegate_id), from_date, to_date,
         reason or None, session.get('username', '?')))
    audit(session.get('username', '?'), user['name'],
          'delegation_add',
          f"Manager #{manager_id} → Delegate #{delegate_id} ({from_date} → {to_date})")
    flash('Delegation added successfully.', 'success')
    return redirect(url_for('admin.delegations_view'))


@admin_bp.route('/delegations/deactivate/<int:deleg_id>', methods=['POST'])
@login_required
@role_required('hr')
def delegations_deactivate(deleg_id):
    validate_csrf()
    role, user = get_current_user()
    redir = _require_user(user)
    if redir:
        return redir
    from models import audit
    d = query_db("SELECT * FROM manager_delegations WHERE id=?", (deleg_id,), one=True)
    if not d:
        flash('Delegation not found.', 'error')
        return redirect(url_for('admin.delegations_view'))
    mutate_db("UPDATE manager_delegations SET active=FALSE WHERE id=?", (deleg_id,))
    audit(session.get('username', '?'), user['name'],
          'delegation_deactivate', f"Delegation #{deleg_id} deactivated")
    flash('Delegation deactivated.', 'success')
    return redirect(url_for('admin.delegations_view'))


# ── Account Lockouts ──────────────────────────────────────────────────────────

@admin_bp.route('/account-lockouts')
@login_required
@role_required('hr')
def account_lockouts_view():
    role, user = get_current_user()
    redir = _require_user(user)
    if redir:
        return redir
    from models import MAX_FAIL_ATTEMPTS, LOCKOUT_MINUTES, audit
    rows = query_db("""
        SELECT al.username, al.fail_count, al.locked_until,
               u.role AS user_role,
               e.name AS emp_name, e.initials
        FROM account_lockouts al
        LEFT JOIN users u ON u.username = al.username
        LEFT JOIN employees e ON e.username = al.username
        ORDER BY al.locked_until DESC
    """)
    lockouts = [dict(r) for r in rows]
    # Add a locked_at field from locked_until minus lockout window for display
    return render_template('account_lockouts.html',
        role=role, user=user, roles=ROLE_PERMISSIONS,
        lockouts=lockouts,
        max_attempts=MAX_FAIL_ATTEMPTS,
        lockout_minutes=LOCKOUT_MINUTES,
        notifications=get_unread_notifications(user['id']))


@admin_bp.route('/account-lockouts/unlock', methods=['POST'])
@login_required
@role_required('hr')
def account_lockouts_unlock():
    validate_csrf()
    role, user = get_current_user()
    redir = _require_user(user)
    if redir:
        return redir
    from models import audit, clear_lockout
    username = request.form.get('username', '').strip()
    if not username:
        flash('No username provided.', 'error')
        return redirect(url_for('admin.account_lockouts_view'))
    clear_lockout(username)
    emp = query_db(
        "SELECT e.id FROM employees e WHERE e.username=?", (username,), one=True)
    if emp:
        add_notification(emp['id'],
            "Your account has been manually unlocked by HR. You can now log in.",
            "success")
    audit(session.get('username', '?'), user['name'],
          'account_unlock', f"Manually unlocked account: {username}")
    flash(f"Account '{username}' has been unlocked.", 'success')
    return redirect(url_for('admin.account_lockouts_view'))
