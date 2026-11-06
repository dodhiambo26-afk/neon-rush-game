import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

DB_PATH = os.getenv("DATABASE_PATH", "tap_tap_rush.db")
_lock = threading.Lock()


def connect():
    con = sqlite3.connect(DB_PATH, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    with _lock:
        con = connect()
        con.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            telegram_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            premium_until TEXT,
            banned INTEGER DEFAULT 0,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER NOT NULL,
            telegram_charge_id TEXT UNIQUE NOT NULL,
            provider_charge_id TEXT,
            amount INTEGER NOT NULL,
            currency TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS scores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER NOT NULL,
            display_name TEXT NOT NULL,
            score INTEGER NOT NULL,
            mode TEXT NOT NULL,
            day_key TEXT NOT NULL,
            duration_seconds INTEGER NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """)
        con.commit()
        con.close()


def upsert_user(uid, username=None, first_name=None, last_name=None):
    now = datetime.now(timezone.utc).isoformat()
    with _lock:
        con = connect()
        con.execute("""
        INSERT INTO users
        (telegram_id, username, first_name, last_name, created_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(telegram_id) DO UPDATE SET
        username=excluded.username,
        first_name=excluded.first_name,
        last_name=excluded.last_name
        """, (uid, username, first_name, last_name, now))
        con.commit()
        con.close()


def get_user(uid):
    with _lock:
        con = connect()
        row = con.execute(
            "SELECT * FROM users WHERE telegram_id=?", (uid,)
        ).fetchone()
        con.close()
        return row


def set_premium(uid, days):
    now = datetime.now(timezone.utc)
    row = get_user(uid)

    current = None
    if row and row["premium_until"]:
        try:
            current = datetime.fromisoformat(row["premium_until"])
        except ValueError:
            current = None

    start = current if current and current > now else now
    expiry = (start + timedelta(days=days)).isoformat()

    with _lock:
        con = connect()
        con.execute(
            "UPDATE users SET premium_until=? WHERE telegram_id=?",
            (expiry, uid)
        )
        con.commit()
        con.close()

    return expiry


def revoke_premium(uid):
    with _lock:
        con = connect()
        con.execute(
            "UPDATE users SET premium_until=NULL WHERE telegram_id=?",
            (uid,)
        )
        con.commit()
        con.close()


def add_payment(uid, charge_id, provider_id, amount, currency):
    with _lock:
        con = connect()
        cur = con.execute("""
        INSERT OR IGNORE INTO payments
        (telegram_id, telegram_charge_id, provider_charge_id,
         amount, currency, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """, (
            uid, charge_id, provider_id, amount, currency,
            datetime.now(timezone.utc).isoformat()
        ))
        con.commit()
        inserted = cur.rowcount == 1
        con.close()
        return inserted


def kenya_day_key():
    return datetime.now(
        ZoneInfo("Africa/Nairobi")
    ).strftime("%Y-%m-%d")


def list_top_scores(limit=10):
    with _lock:
        con = connect()
        rows = con.execute("""
        SELECT display_name, MAX(score) AS score, telegram_id
        FROM scores
        WHERE day_key=? AND mode='tournament'
        GROUP BY telegram_id
        ORDER BY score DESC
        LIMIT ?
        """, (kenya_day_key(), limit)).fetchall()
        con.close()
        return rows


def save_score(uid, display_name, score, mode, duration):
    with _lock:
        con = connect()
        con.execute("""
        INSERT INTO scores
        (telegram_id, display_name, score, mode,
         day_key, duration_seconds, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            uid, display_name, score, mode,
            kenya_day_key(), duration,
            datetime.now(timezone.utc).isoformat()
        ))
        con.commit()
        con.close()


def is_banned(uid):
    row = get_user(uid)
    return bool(row and row["banned"])


def set_banned(uid, value):
    with _lock:
        con = connect()
        con.execute(
            "UPDATE users SET banned=? WHERE telegram_id=?",
            (1 if value else 0, uid)
        )
        con.commit()
        con.close()


def set_premium_price(stars):
    with _lock:
        con = connect()
        con.execute("""
        INSERT INTO settings(key, value)
        VALUES('premium_price', ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value
        """, (str(stars),))
        con.commit()
        con.close()


def get_premium_price(default):
    with _lock:
        con = connect()
        row = con.execute(
            "SELECT value FROM settings WHERE key='premium_price'"
        ).fetchone()
        con.close()

    try:
        return int(row["value"]) if row else default
    except (ValueError, TypeError):
        return default


def get_stats():
    with _lock:
        con = connect()

        players = con.execute(
            "SELECT COUNT(*) AS n FROM users"
        ).fetchone()["n"]

        now = datetime.now(timezone.utc).isoformat()

        premium = con.execute(
            "SELECT COUNT(*) AS n FROM users WHERE premium_until>?",
            (now,)
        ).fetchone()["n"]

        payments = con.execute(
            "SELECT COUNT(*) AS n FROM payments"
        ).fetchone()["n"]

        stars = con.execute(
            "SELECT COALESCE(SUM(amount),0) AS n "
            "FROM payments WHERE currency='XTR'"
        ).fetchone()["n"]

        con.close()

    return {
        "players": players,
        "premium": premium,
        "payments": payments,
        "stars": stars
      }
