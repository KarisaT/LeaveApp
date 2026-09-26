# Groot Leave Management System

A Flask web app for managing employee leave requests, approvals, and HR administration.

## Setup

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Run the app
python app.py
```

Then open http://127.0.0.1:5000 in your browser.

## Roles

Switch roles using the dropdown in the top-left sidebar:

| Role         | Access                                              |
|--------------|-----------------------------------------------------|
| Employee     | Apply for leave, view own history & balances        |
| Manager      | Approve/reject requests, view team calendar         |
| HR Admin     | Full control: all requests, employees, holidays     |
| Payroll View | Read-only: leave summaries and reports              |

## Features

- Leave application & approval workflow
- Leave balance tracking per employee
- Interactive leave calendar
- Employee profiles & directory
- Public holidays management (HR can add/delete)
- Reports & analytics dashboard
- Role-based navigation and permissions
- Notifications

## Project Structure

```
groot_leave/
├── app.py              # Main Flask app & routes
├── requirements.txt
├── templates/
│   ├── base.html       # Shared layout, sidebar, nav
│   ├── dashboard.html
│   ├── apply.html
│   ├── my_leave.html
│   ├── approvals.html
│   ├── calendar.html
│   ├── employees.html
│   ├── holidays.html
│   ├── reports.html
│   ├── notifications.html
│   └── profile.html
└── static/             # Add custom CSS/JS here
```

## Next Steps (Production)

- Replace in-memory data with a database (SQLAlchemy + PostgreSQL/MySQL)
- Add real user authentication (Flask-Login)
- Add email notifications (Flask-Mail)
- Deploy with Gunicorn + Nginx
