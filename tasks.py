from datetime import date

from database import get_db
from helpers import LEAVE_ENTITLEMENTS


def run_monthly_carry_forward():
    """Add 2 annual leave days to every employee once per calendar month."""
    CARRY_DAYS = 2
    MAX_ANNUAL = LEAVE_ENTITLEMENTS['Annual Leave']['max']  # 21
    this_month = date.today().strftime('%Y-%m')

    db = get_db()
    cur = db.cursor()

    cur.execute("SELECT 1 FROM carry_forward_log WHERE run_month=%s", (this_month,))
    if cur.fetchone():
        cur.close()
        return

    cur.execute("SELECT id, name FROM employees")
    employees_list = cur.fetchall()
    updated = 0
    for emp in employees_list:
        cur.execute(
            "SELECT days FROM leave_balances WHERE employee_id=%s AND leave_key='annual'",
            (emp['id'],)
        )
        bal_row = cur.fetchone()
        if bal_row is not None:
            new_days = min(MAX_ANNUAL, bal_row['days'] + CARRY_DAYS)
            cur.execute(
                "UPDATE leave_balances SET days=%s WHERE employee_id=%s AND leave_key='annual'",
                (new_days, emp['id']))
        else:
            cur.execute(
                "INSERT INTO leave_balances (employee_id,leave_key,days) VALUES (%s,%s,%s)",
                (emp['id'], 'annual', CARRY_DAYS))
        cur.execute(
            "INSERT INTO notifications (employee_id,message,time_label,type,is_read) VALUES (%s,%s,%s,%s,0)",
            (emp['id'],
             f"+{CARRY_DAYS} annual leave days added automatically for {date.today().strftime('%B %Y')}.",
             "Just now", "success"))
        updated += 1

    cur.execute(
        "INSERT INTO carry_forward_log (run_month, employees_updated) VALUES (%s,%s)",
        (this_month, updated))
    db.commit()
    cur.close()


def run_year_end_carryover():
    """Cap every employee's annual leave balance at 10 days at the start of each year."""
    CARRY_OVER_CAP = 10
    BASE_ENTITLEMENT = LEAVE_ENTITLEMENTS['Annual Leave']['max']  # 21

    this_year = str(date.today().year)
    if date.today().month != 1:
        return

    db = get_db()
    cur = db.cursor()

    cur.execute("SELECT 1 FROM year_end_carryover_log WHERE run_year=%s", (this_year,))
    if cur.fetchone():
        cur.close()
        return

    cur.execute("SELECT id, name FROM employees")
    employees_list = cur.fetchall()
    updated = 0
    for emp in employees_list:
        cur.execute(
            "SELECT days FROM leave_balances WHERE employee_id=%s AND leave_key='annual'",
            (emp['id'],)
        )
        bal_row = cur.fetchone()

        current_days = bal_row['days'] if bal_row else 0
        carried = min(current_days, CARRY_OVER_CAP)
        new_days = min(BASE_ENTITLEMENT + carried, BASE_ENTITLEMENT + CARRY_OVER_CAP)
        forfeited = current_days - carried

        if bal_row is not None:
            cur.execute(
                "UPDATE leave_balances SET days=%s WHERE employee_id=%s AND leave_key='annual'",
                (new_days, emp['id']))
        else:
            cur.execute(
                "INSERT INTO leave_balances (employee_id,leave_key,days) VALUES (%s,%s,%s)",
                (emp['id'], 'annual', new_days))

        if forfeited > 0:
            msg = (f"New year balance reset: {carried} day(s) carried over "
                   f"(+{forfeited} forfeited — 10-day carry-over cap). "
                   f"New annual balance: {new_days} days.")
        else:
            msg = (f"New year balance reset: {carried} day(s) carried over. "
                   f"New annual balance: {new_days} days.")
        cur.execute(
            "INSERT INTO notifications (employee_id,message,time_label,type,is_read) VALUES (%s,%s,%s,%s,0)",
            (emp['id'], msg, "Just now", "info"))
        updated += 1

    cur.execute(
        "INSERT INTO year_end_carryover_log (run_year, employees_updated) VALUES (%s,%s)",
        (this_year, updated))
    db.commit()
    cur.close()
