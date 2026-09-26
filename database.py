import logging
import os
import psycopg2
import psycopg2.extras
from flask import g
import logging
import os
import psycopg2
import psycopg2.extras
from flask import g
from dotenv import load_dotenv
load_dotenv()

import psycopg2
import psycopg2.extras



import psycopg2
import psycopg2.extras

logger = logging.getLogger(__name__)

DATABASE_URL = os.environ.get('DATABASE_URL')
if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL environment variable is not set. "
        "Set it to your PostgreSQL connection string before starting the app."
    )

def get_db():
    db = getattr(g, '_database', None)
    if db is None or db.closed:
        db = g._database = psycopg2.connect(
            DATABASE_URL,
            sslmode='require',   # ← ADD THIS
            cursor_factory=psycopg2.extras.RealDictCursor
        )
        db.autocommit = False
    return db



def close_db(exception=None):
    db = getattr(g, '_database', None)
    if db is not None and not db.closed:
        db.close()


# ── Query helpers ─────────────────────────────────────────────────────────────

def query_db(sql, args=(), one=False):
    # Convert SQLite ? placeholders to PostgreSQL %s
    sql = sql.replace('?', '%s')
    try:
        cur = get_db().cursor()
        cur.execute(sql, args)
        rv = cur.fetchall()
        cur.close()
        rows = [dict(r) for r in rv]
        return (rows[0] if rows else None) if one else rows
    except Exception as exc:
        # Roll back on error so the connection stays usable
        try:
            get_db().rollback()
        except Exception:
            pass
        logger.exception("query_db failed | sql=%r args=%r | %s", sql, args, exc)
        raise


def mutate_db(sql, args=()):
    sql = sql.replace('?', '%s')
    db = get_db()
    try:
        cur = db.cursor()
        cur.execute(sql, args)
        db.commit()
        cur.close()
    except Exception as exc:
        try:
            db.rollback()
        except Exception:
            pass
        logger.exception("mutate_db failed | sql=%r args=%r | %s", sql, args, exc)
        raise


def mutate_db_returning(sql, args=()):
    """Use this when you need the last inserted id."""
    sql = sql.replace('?', '%s')
    db = get_db()
    cur = db.cursor()
    cur.execute(sql, args)
    db.commit()
    # Extract lastrowid from RETURNING clause if present
    try:
        row = cur.fetchone()
        last_id = row[list(row.keys())[0]] if row else None
    except Exception:
        last_id = None
    cur.close()
    return last_id


def row_to_dict(row):
    return dict(row) if row else None
