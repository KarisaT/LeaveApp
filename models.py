import hashlib
import hmac
import secrets
from datetime import datetime, timedelta
from flask import session, abort, request

from database import get_db, query_db, mutate_db


# ── Schema ────────────────────────────────────────────────────────────────────

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    username      TEXT PRIMARY KEY,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL CHECK(role IN ('employee','manager','hr','payroll'))
);

CREATE TABLE IF NOT EXISTS employees (
    id         SERIAL PRIMARY KEY,
    name       TEXT NOT NULL,
    initials   TEXT NOT NULL,
    dept       TEXT NOT NULL,
    role_title TEXT NOT NULL,
    email      TEXT NOT NULL,
    start_date TEXT NOT NULL,
    manager_id INTEGER REFERENCES employees(id),
    gender     TEXT NOT NULL DEFAULT 'male' CHECK(gender IN ('male','female')),
    username   TEXT REFERENCES users(username)
);

CREATE TABLE IF NOT EXISTS leave_balances (
    employee_id   INTEGER NOT NULL REFERENCES employees(id),
    leave_key     TEXT NOT NULL,
    days          INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (employee_id, leave_key)
);

CREATE TABLE IF NOT EXISTS leave_requests (
    id               SERIAL PRIMARY KEY,
    employee_id      INTEGER NOT NULL REFERENCES employees(id),
    leave_type       TEXT NOT NULL,
    from_date        TEXT NOT NULL,
    to_date          TEXT NOT NULL,
    days             INTEGER NOT NULL,
    status           TEXT NOT NULL DEFAULT 'pending'
                         CHECK(status IN ('pending','approved','rejected','cancelled')),
    reason           TEXT,
    applied_date     TEXT NOT NULL,
    rejection_reason TEXT,
    sick_form_filename TEXT
);

CREATE TABLE IF NOT EXISTS parental_leave_grants (
    id            SERIAL PRIMARY KEY,
    employee_id   INTEGER NOT NULL REFERENCES employees(id),
    leave_type    TEXT NOT NULL CHECK(leave_type IN ('Maternity Leave','Paternity Leave')),
    from_date     TEXT NOT NULL,
    to_date       TEXT NOT NULL,
    days          INTEGER NOT NULL,
    notes         TEXT,
    granted_by    TEXT NOT NULL,
    granted_at    TEXT NOT NULL DEFAULT (to_char(NOW() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')),
    status        TEXT NOT NULL DEFAULT 'approved'
);

CREATE TABLE IF NOT EXISTS public_holidays (
    id        SERIAL PRIMARY KEY,
    date      TEXT NOT NULL UNIQUE,
    name      TEXT NOT NULL,
    type      TEXT NOT NULL DEFAULT 'National Holiday'
);

CREATE TABLE IF NOT EXISTS notifications (
    id          SERIAL PRIMARY KEY,
    employee_id INTEGER NOT NULL REFERENCES employees(id),
    message     TEXT NOT NULL,
    time_label  TEXT NOT NULL DEFAULT 'Just now',
    type        TEXT NOT NULL DEFAULT 'info',
    is_read     INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS login_attempts (
    id           SERIAL PRIMARY KEY,
    ip_address   TEXT NOT NULL,
    username     TEXT NOT NULL,
    attempted_at TEXT NOT NULL DEFAULT (to_char(NOW() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS'))
);

CREATE TABLE IF NOT EXISTS account_lockouts (
    username     TEXT PRIMARY KEY,
    locked_until TEXT NOT NULL,
    fail_count   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS audit_log (
    id             SERIAL PRIMARY KEY,
    actor_username TEXT NOT NULL,
    actor_name     TEXT NOT NULL,
    action         TEXT NOT NULL,
    target_desc    TEXT,
    logged_at      TEXT NOT NULL DEFAULT (to_char(NOW() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS'))
);

CREATE TABLE IF NOT EXISTS carry_forward_log (
    id                SERIAL PRIMARY KEY,
    run_month         TEXT NOT NULL UNIQUE,
    run_at            TEXT NOT NULL DEFAULT (to_char(NOW() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')),
    employees_updated INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS year_end_carryover_log (
    id                SERIAL PRIMARY KEY,
    run_year          TEXT NOT NULL UNIQUE,
    run_at            TEXT NOT NULL DEFAULT (to_char(NOW() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')),
    employees_updated INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS manager_delegations (
    id            SERIAL PRIMARY KEY,
    manager_id    INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    delegate_id   INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    from_date     TEXT NOT NULL,
    to_date       TEXT NOT NULL,
    reason        TEXT,
    created_by    TEXT NOT NULL,
    created_at    TEXT NOT NULL DEFAULT (to_char(NOW() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')),
    active        BOOLEAN NOT NULL DEFAULT TRUE
);
"""

# ── Password utils ────────────────────────────────────────────────────────────

def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 260_000)
    return f"pbkdf2:sha256:260000${salt}${dk.hex()}"


def check_password(stored: str, provided: str) -> bool:
    try:
        parts = stored.split('$')
        iters = int(parts[0].split(':')[-1])
        salt, stored_dk = parts[1], bytes.fromhex(parts[2])
        dk = hashlib.pbkdf2_hmac('sha256', provided.encode(), salt.encode(), iters)
        return hmac.compare_digest(dk, stored_dk)
    except Exception:
        return False


# ── CSRF ──────────────────────────────────────────────────────────────────────

def generate_csrf_token():
    if 'csrf_token' not in session:
        session['csrf_token'] = secrets.token_hex(32)
    return session['csrf_token']


def validate_csrf():
    token = request.form.get('csrf_token') or request.headers.get('X-CSRF-Token')
    if not token or not hmac.compare_digest(token, session.get('csrf_token', '')):
        abort(403)


# ── Account lockout ───────────────────────────────────────────────────────────

MAX_FAIL_ATTEMPTS = 5
LOCKOUT_MINUTES = 15


def is_locked(username):
    """Return (locked: bool, seconds_remaining: int)."""
    row = query_db(
        "SELECT locked_until, fail_count FROM account_lockouts WHERE username=%s",
        (username,), one=True)
    if not row:
        return False, 0
    until = datetime.fromisoformat(row['locked_until'])
    now = datetime.utcnow()
    if now < until:
        return True, int((until - now).total_seconds())
    return False, 0


def record_fail(username):
    """Increment failure counter; return new count."""
    row = query_db(
        "SELECT fail_count FROM account_lockouts WHERE username=%s", (username,), one=True)
    new_count = (row['fail_count'] if row else 0) + 1
    locked_until = (datetime.utcnow() + timedelta(minutes=LOCKOUT_MINUTES)).isoformat()
    if row:
        mutate_db(
            "UPDATE account_lockouts SET fail_count=%s, locked_until=%s WHERE username=%s",
            (new_count, locked_until, username))
    else:
        mutate_db(
            "INSERT INTO account_lockouts (username, locked_until, fail_count) VALUES (%s,%s,%s)",
            (username, locked_until, new_count))
    return new_count


def clear_lockout(username):
    mutate_db("DELETE FROM account_lockouts WHERE username=%s", (username,))


# ── Audit log ─────────────────────────────────────────────────────────────────

def audit(actor_username, actor_name, action, target_desc=None):
    mutate_db(
        "INSERT INTO audit_log (actor_username,actor_name,action,target_desc) VALUES (%s,%s,%s,%s)",
        (actor_username, actor_name, action, target_desc))


# ── Seed & init ───────────────────────────────────────────────────────────────
def init_db(app):
    with app.app_context():
        db = get_db()
        cur = db.cursor()

        # Create all tables
        for statement in SCHEMA.strip().split(';'):
            s = statement.strip()
            if s:
                cur.execute(s)
        db.commit()

        # Migrations: add columns if missing
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name='leave_requests' AND column_name='sick_form_filename'
        """)
        if not cur.fetchone():
            cur.execute("ALTER TABLE leave_requests ADD COLUMN sick_form_filename TEXT")
            db.commit()

        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name='employees' AND column_name='gender'
        """)
        if not cur.fetchone():
            cur.execute("ALTER TABLE employees ADD COLUMN gender TEXT NOT NULL DEFAULT 'male' "
                        "CHECK(gender IN ('male','female'))")
            cur.execute("UPDATE employees SET gender='female' WHERE id IN (1,3,5)")
            db.commit()

        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name='employees' AND column_name='username'
        """)
        if not cur.fetchone():
            cur.execute("ALTER TABLE employees ADD COLUMN username TEXT")
            db.commit()

        # Add phone column if missing
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name='employees' AND column_name='phone'
        """)
        if not cur.fetchone():
            cur.execute("ALTER TABLE employees ADD COLUMN phone TEXT DEFAULT ''")
            db.commit()

        # Backfill usernames for seed employees
        seed_username_map = {
            1: 'jane.mwangi',
            2: 'samuel.otieno',
            3: 'aisha.omar',
            4: 'brian.waweru',
            5: 'lilian.muthoni',
            6: 'kevin.njoroge',
        }
        for emp_id, uname in seed_username_map.items():
            cur.execute(
                "UPDATE employees SET username=%s WHERE id=%s AND (username IS NULL OR username='')",
                (uname, emp_id))
        db.commit()

        # Seed only if empty
        cur.execute("SELECT COUNT(*) FROM users")
        if cur.fetchone()['count'] > 0:
            cur.close()
            return

        seed_users = [
            ('jane.mwangi',   'Pass1234!', 'employee'),
            ('samuel.otieno', 'Pass1234!', 'manager'),
            ('aisha.omar',    'Pass1234!', 'employee'),
            ('brian.waweru',  'Pass1234!', 'hr'),
            ('lilian.muthoni','Pass1234!', 'employee'),
            ('kevin.njoroge', 'Pass1234!', 'payroll'),
        ]
        for u, pw, r in seed_users:
            cur.execute("INSERT INTO users VALUES (%s,%s,%s)", (u, hash_password(pw), r))
        db.commit()

        emp_rows = [
            (1,'Jane Mwangi',   'JM','Engineering','Software Engineer',  'j.mwangi@groot.co.ke',  '2019-03-12','female','jane.mwangi'),
            (2,'Samuel Otieno', 'SO','Engineering','Engineering Manager', 's.otieno@groot.co.ke',  '2017-06-01','male',  'samuel.otieno'),
            (3,'Aisha Omar',    'AO','Finance',     'Finance Analyst',    'a.omar@groot.co.ke',    '2020-09-15','female','aisha.omar'),
            (4,'Brian Waweru',  'BW','HR',           'HR Officer',         'b.waweru@groot.co.ke',  '2018-01-10','male',  'brian.waweru'),
            (5,'Lilian Muthoni','LM','IT',           'Systems Admin',      'l.muthoni@groot.co.ke', '2021-04-20','female','lilian.muthoni'),
            (6,'Kevin Njoroge', 'KN','Sales',        'Sales Executive',    'k.njoroge@groot.co.ke', '2022-02-14','male',  'kevin.njoroge'),
        ]
        for row in emp_rows:
            cur.execute(
                "INSERT INTO employees (id,name,initials,dept,role_title,email,start_date,gender,username) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)", row)
        # Update sequences after manual ID inserts
        cur.execute("SELECT setval('employees_id_seq', (SELECT MAX(id) FROM employees))")

        for emp_id, mgr_id in {1: 2, 3: 4, 5: 2}.items():
            cur.execute("UPDATE employees SET manager_id=%s WHERE id=%s", (mgr_id, emp_id))

        bal_rows = [
            (1,'annual',14),(1,'sick',25),(1,'emergency',3),(1,'compassionate',3),(1,'maternity',90),
            (2,'annual',10),(2,'sick',28),(2,'emergency',3),(2,'compassionate',3),(2,'paternity',10),
            (3,'annual',18),(3,'sick',30),(3,'emergency',3),(3,'compassionate',3),(3,'maternity',90),
            (4,'annual',12),(4,'sick',24),(4,'emergency',3),(4,'compassionate',3),(4,'paternity',10),
            (5,'annual',7), (5,'sick',20),(5,'emergency',3),(5,'compassionate',3),(5,'maternity',90),
            (6,'annual',9), (6,'sick',16),(6,'emergency',3),(6,'compassionate',3),(6,'paternity',10),
        ]
        for row in bal_rows:
            cur.execute("INSERT INTO leave_balances VALUES (%s,%s,%s)", row)

        req_rows = [
            (1,1,'Annual Leave',            '2026-06-02','2026-06-05',4, 'pending',  'Family vacation',    '2026-05-20',None),
            (2,6,'Sick Leave',              '2026-05-28','2026-05-30',3, 'approved', 'Medical',            '2026-05-27',None),
            (3,3,'Maternity/Paternity Leave','2026-07-01','2026-09-30',65,'pending', 'Maternity',          '2026-05-18',None),
            (4,4,'Annual Leave',            '2026-06-10','2026-06-12',2, 'pending',  'Personal',           '2026-05-19',None),
            (5,5,'Emergency Leave',         '2026-06-20','2026-06-25',4, 'rejected', 'Professional course','2026-05-15','Insufficient emergency leave balance.'),
        ]
        for row in req_rows:
            cur.execute(
                "INSERT INTO leave_requests "
                "(id,employee_id,leave_type,from_date,to_date,days,status,reason,applied_date,rejection_reason) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                row
            )
        cur.execute("SELECT setval('leave_requests_id_seq', (SELECT MAX(id) FROM leave_requests))")

        hol_rows = [
            (1,'2026-01-01',"New Year's Day", 'National Holiday'),
            (2,'2026-05-01','Labour Day',      'National Holiday'),
            (3,'2026-06-01','Madaraka Day',    'National Holiday'),
            (4,'2026-10-20','Mashujaa Day',    'National Holiday'),
            (5,'2026-12-12','Jamhuri Day',     'National Holiday'),
            (6,'2026-12-25','Christmas Day',   'National Holiday'),
            (7,'2026-12-26','Boxing Day',      'National Holiday'),
        ]
        for row in hol_rows:
            cur.execute("INSERT INTO public_holidays VALUES (%s,%s,%s,%s)", row)
        cur.execute("SELECT setval('public_holidays_id_seq', (SELECT MAX(id) FROM public_holidays))")

        notif_rows = [
            (1,1,'Your Annual Leave (2–5 Jun) has been submitted',   '2 hours ago','success',0),
            (2,2,"Kevin Njoroge's leave ends tomorrow",              '5 hours ago','warning',0),
            (3,4,'New leave application from Aisha Omar (Maternity)','Yesterday',  'info',   0),
            (4,2,'4 leave requests pending your approval',           'Yesterday',  'warning',0),
            (5,1,'Madaraka Day (1 Jun) — public holiday in 10 days', '2 days ago', 'info',   1),
        ]
        for row in notif_rows:
            cur.execute("INSERT INTO notifications VALUES (%s,%s,%s,%s,%s,%s)", row)
        cur.execute("SELECT setval('notifications_id_seq', (SELECT MAX(id) FROM notifications))")

        db.commit()
        cur.close()
