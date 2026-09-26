from datetime import datetime, timedelta, date
from flask import session

from database import query_db, mutate_db

# ── Role / permission config ──────────────────────────────────────────────────

ROLE_PERMISSIONS = {
    'employee': {
        'label': 'Employee', 'employee_id': 1,
        'allowed': {'dashboard','apply_leave','my_leave','cancel_leave','calendar',
                    'holidays_view','notifications_view','mark_notification_read','profile','change_password','logout'},
    },
    'manager': {
        'label': 'Manager', 'employee_id': 2,
        'allowed': {'dashboard','apply_leave','my_leave','cancel_leave',
                    'approvals','approve_request','reject_request','bulk_approve','bulk_reject',
                    'employees_view','calendar','holidays_view',
                    'delegations_view',
                    'notifications_view','mark_notification_read','profile','change_password','logout'},
    },
    'hr': {
        'label': 'HR Admin', 'employee_id': 4,
        'allowed': {'dashboard','apply_leave','my_leave','cancel_leave',
                    'approvals','approve_request','reject_request','bulk_approve','bulk_reject',
                    'employees_view','calendar','holidays_view','delete_holiday',
                    'reports','audit_log_view',
                    'delegations_view','delegations_add','delegations_deactivate',
                    'account_lockouts_view','account_lockouts_unlock',
                    'notifications_view','mark_notification_read','profile','change_password','logout'},
    },
    'payroll': {
        'label': 'Payroll View', 'employee_id': 6,
        'allowed': {'dashboard','reports','employees_view','calendar',
                    'holidays_view','notifications_view','mark_notification_read','profile','change_password','logout'},
    },
}

LEAVE_ENTITLEMENTS = {
    "Annual Leave":        {"key": "annual",        "max": 21,  "gender": None},
    "Sick Leave":          {"key": "sick",          "max": 30,  "gender": None},
    "Emergency Leave":     {"key": "emergency",     "max": 3,   "gender": None},
    "Compassionate Leave": {"key": "compassionate", "max": 3,   "gender": None},
    "Maternity Leave":     {"key": "maternity",     "max": 90,  "gender": "female"},
    "Paternity Leave":     {"key": "paternity",     "max": 10,  "gender": "male"},
}

# ── Employee helpers ──────────────────────────────────────────────────────────

def get_employee(emp_id):
    row = query_db("SELECT * FROM employees WHERE id=%s", (emp_id,), one=True)
    if not row:
        return None
    emp = dict(row)
    emp.setdefault('phone', None)   # column may not exist in older DB schemas
    emp.setdefault('username', None)
    return emp


def get_balances(emp_id):
    """Return a complete balances dict with 0 defaults for missing keys."""
    rows = query_db("SELECT leave_key, days FROM leave_balances WHERE employee_id=%s", (emp_id,))
    defaults = {'annual': 0, 'sick': 0, 'emergency': 0, 'compassionate': 0,
                'maternity': 0, 'paternity': 0}
    result = dict(defaults)
    for r in rows:
        result[r['leave_key']] = r['days']
    return result


def get_current_user():
    role = session.get('role')
    if not role:
        return None, None
    emp_id = session.get('employee_id')
    if emp_id:
        return role, get_employee(emp_id)
    return role, None


# ── Leave helpers ─────────────────────────────────────────────────────────────

def get_public_holiday_dates():
    rows = query_db("SELECT date FROM public_holidays")
    return {datetime.strptime(r['date'], "%Y-%m-%d").date() for r in rows}


def count_working_days(from_str, to_str):
    d1 = datetime.strptime(from_str, "%Y-%m-%d").date()
    d2 = datetime.strptime(to_str,   "%Y-%m-%d").date()
    if d2 < d1:
        return 0
    holiday_dates = get_public_holiday_dates()
    count, current = 0, d1
    while current <= d2:
        if current.weekday() < 5 and current not in holiday_dates:
            count += 1
        current += timedelta(days=1)
    return count


def has_overlap(employee_id, from_str, to_str, exclude_id=None):
    sql = """SELECT 1 FROM leave_requests
             WHERE employee_id=%s AND status IN ('pending','approved')
             AND NOT (to_date < %s OR from_date > %s)"""
    args = [employee_id, from_str, to_str]
    if exclude_id:
        sql += " AND id != %s"
        args.append(exclude_id)
    return bool(query_db(sql, args, one=True))


def deduct_balance(employee_id, leave_type, days):
    info = LEAVE_ENTITLEMENTS.get(leave_type)
    if not info or not info["key"]:
        return
    mutate_db(
        "UPDATE leave_balances SET days=MAX(0,days-%s) WHERE employee_id=%s AND leave_key=%s",
        (days, employee_id, info["key"]))


def restore_balance(employee_id, leave_type, days):
    info = LEAVE_ENTITLEMENTS.get(leave_type)
    if not info or not info["key"]:
        return
    mutate_db(
        "UPDATE leave_balances SET days=MIN(%s,days+%s) WHERE employee_id=%s AND leave_key=%s",
        (info["max"], days, employee_id, info["key"]))


# ── Notification helpers ──────────────────────────────────────────────────────

def add_notification(employee_id, message, notif_type="info"):
    mutate_db(
        "INSERT INTO notifications (employee_id,message,time_label,type,is_read) VALUES (%s,%s,%s,%s,0)",
        (employee_id, message, "Just now", notif_type))


def get_unread_notifications(employee_id):
    rows = query_db(
        "SELECT * FROM notifications WHERE employee_id=%s AND is_read=0", (employee_id,))
    return [dict(r) for r in rows]
