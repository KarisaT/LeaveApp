# Running Leave App with Docker

## Files to add to your project root

Copy these files into the **same folder as `app.py`**:

```
your-project/
├── app.py
├── database.py
├── models.py
├── helpers.py
├── decorators.py
├── blueprints/
├── templates/
├── Dockerfile          ← add this
├── docker-compose.yml  ← add this
├── .dockerignore       ← add this
└── requirements.txt    ← add this (or update if you have one)
```

---

## Quick Start (recommended)

```bash
# 1. Build and start the container
docker compose up --build

# 2. Open in browser
http://localhost:5000
```

The SQLite database is stored in a Docker volume (`leave_data`) —
it survives container restarts and rebuilds.

---

## Common Commands

| Task | Command |
|------|---------|
| Start in background | `docker compose up -d --build` |
| Stop | `docker compose down` |
| View logs | `docker compose logs -f` |
| Restart | `docker compose restart` |
| Open shell inside container | `docker compose exec leave-app bash` |
| Delete everything incl. DB | `docker compose down -v` |

---

## Before Deploying to Production

1. **Change the SECRET_KEY** in `docker-compose.yml`:
   ```yaml
   SECRET_KEY: "your-long-random-secret-key-here"
   ```
   Generate one with:
   ```bash
   python3 -c "import secrets; print(secrets.token_hex(32))"
   ```

2. **Use an `.env` file** instead of hardcoding the secret:
   ```env
   # .env  (never commit this file)
   SECRET_KEY=your-long-random-secret-key-here
   ```
   Then in `docker-compose.yml`:
   ```yaml
   environment:
     SECRET_KEY: ${SECRET_KEY}
   ```

3. **Put Nginx in front** if you're exposing this to the internet —
   Nginx handles HTTPS/SSL, Gunicorn handles Python.

---

## How it Works

```
Browser → port 5000
              ↓
        [Docker container]
           Gunicorn (WSGI)
              ↓
           Flask app
              ↓
        SQLite (leave.db)
        stored in Docker volume
```

- **Gunicorn** replaces `flask run` — it's production-grade and handles
  multiple requests at once (2 workers configured).
- **Docker volume** (`leave_data`) keeps the database safe even when
  you rebuild the image.
- **Non-root user** inside the container for basic security hardening.

---

## Changing the Port

To run on port 8080 instead of 5000, edit `docker-compose.yml`:
```yaml
ports:
  - "8080:5000"   # host port 8080 → container port 5000
```
