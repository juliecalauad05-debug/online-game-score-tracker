"""
db.py - database access for the Score Tracker.

MySQL (through PyMySQL) is the main database. For a quick demo, or for the
automated tests, set  DB_BACKEND=sqlite  and the same app runs on a SQLite file.
All SQL in app.py is written to work on both, so one code path is tested.

Environment variables (all optional):
    DB_BACKEND   mysql (default) or sqlite
    DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME   MySQL connection
    DB_PATH      SQLite file (default: scoretracker.db next to this file)
"""
import os
import sqlite3
from datetime import datetime
from decimal import Decimal

from flask import g

HERE = os.path.dirname(os.path.abspath(__file__))
DT_FORMAT = "%Y-%m-%d %H:%M:%S"


def backend():
    return os.environ.get("DB_BACKEND", "mysql").lower()


def sqlite_path():
    return os.environ.get("DB_PATH", os.path.join(HERE, "scoretracker.db"))


def connect(use_database=True):
    """Open a new connection (autocommit). Used by get_db() and init_db.py."""
    if backend() == "sqlite":
        con = sqlite3.connect(sqlite_path(), isolation_level=None)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")  # makes ON DELETE CASCADE work
        return con
    import pymysql
    cfg = dict(
        host=os.environ.get("DB_HOST", "localhost"),
        port=int(os.environ.get("DB_PORT", "3306")),
        user=os.environ.get("DB_USER", "root"),
        password=os.environ.get("DB_PASSWORD", ""),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
    )
    if use_database:
        cfg["database"] = os.environ.get("DB_NAME", "score_tracker")
    return pymysql.connect(**cfg)


def get_db():
    """One connection per request, stored on flask.g."""
    if "db" not in g:
        g.db = connect()
    return g.db


def close_db(_exc=None):
    con = g.pop("db", None)
    if con is not None:
        con.close()


def init_app(app):
    app.teardown_appcontext(close_db)


# ---------------------------------------------------------------- helpers
def now():
    """Current local time without microseconds (we store times from Python so
    MySQL and SQLite behave the same)."""
    return datetime.now().replace(microsecond=0)


def parse_dt(value):
    """MySQL returns datetime objects, SQLite returns text. Accept both."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    return datetime.strptime(str(value)[:19], DT_FORMAT)


def _prep(sql, args):
    args = tuple(args)
    if backend() == "sqlite":
        args = tuple(a.strftime(DT_FORMAT) if isinstance(a, datetime) else a for a in args)
        sql = sql.replace("%s", "?")
    return sql, args


def _clean(row):
    """MySQL returns SUM() as Decimal; turn it into int/float."""
    out = {}
    for key, val in dict(row).items():
        if isinstance(val, Decimal):
            val = int(val) if val == val.to_integral_value() else float(val)
        out[key] = val
    return out


def query(sql, args=(), one=False):
    """Run a SELECT. Returns a list of dicts, or one dict/None when one=True."""
    sql, args = _prep(sql, args)
    con = get_db()
    if backend() == "sqlite":
        rows = [_clean(r) for r in con.execute(sql, args).fetchall()]
    else:
        with con.cursor() as cur:
            cur.execute(sql, args)
            rows = [_clean(r) for r in cur.fetchall()]
    if one:
        return rows[0] if rows else None
    return rows


def execute(sql, args=()):
    """Run INSERT/UPDATE/DELETE. Returns the new row id for INSERTs."""
    sql, args = _prep(sql, args)
    con = get_db()
    if backend() == "sqlite":
        return con.execute(sql, args).lastrowid
    with con.cursor() as cur:
        cur.execute(sql, args)
        return cur.lastrowid


def scalar(sql, args=()):
    """Return the first column of the first row (for COUNT/SUM queries)."""
    row = query(sql, args, one=True)
    return list(row.values())[0] if row else None
