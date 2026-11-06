
import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

DB_PATH = os.getenv("DATABASE_PATH", "tap_tap_rush.db")
KENYA = ZoneInfo("Africa/Nairobi")
_lock = threading.Lock()

TOURNAMENT_STARS = 8


def connect():
    con = sqlite3.connect(DB_PATH, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con


def tournament_session():
    """Returns the active tournament session: 6 AM to 6 AM Kenya time."""
    now = datetime.now(KENYA)

    start = now.replace(
        hour=6, minute=0, second=0, microsecond=0
    )

    if now < start:
        start -= timedelta(days=1)

    end = start + timedelta(days=1)

    return {
        "key": start.strftime("%Y-%m-%d"),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "starts_at": "06:00 AM",
        "ends_at": "06:00 AM"
    }


def kenya_day_key():
    # Kept for compatibility with the existing backend.
    return tournament_session()["key"]


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

        CREATE TABLE IF NOT EXISTS tournament_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER NOT NULL,
            session_key TEXT NOT NULL,
            payment_id INTEGER,
            created_at TEXT NOT NULL,
            UNIQUE(telegram_id, session_key)
        );

        CREATE INDEX IF NOT EXISTS idx_scores_tournament
        ON scores(mode, day_key, telegram_id, score);

        CREATE INDEX IF NOT EXISTS idx_entries_session
        ON tournament_entries(session_key, telegram_id);
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
            "SELECT * FROM users WHERE telegram_id=?",
            (uid,)
        ).fetchone()
        con.close()
        return row


def grant_tournament_entry(uid, session_key=None, payment_id=None):
    if session_key is None:
        session_key = tournament_session()["key"]

    with _lock:
        con = connect()
        con.execute("""
            INSERT OR IGNORE INTO tournament_entries
            (telegram_id, session_key, payment_id, created_at)
            VALUES (?, ?, ?, ?)
        """, (
            uid,
            session_key,
            payment_id,
            datetime.now(timezone.utc).isoformat()
        ))

        con.commit()

        row = con.execute("""
            SELECT id FROM tournament_entries
            WHERE telegram_id=? AND session_key=?
        """, (uid, session_key)).fetchone()

        con.close()
        return row is not None


def has_tournament_entry(uid, session_key=None):
    if session_key is None:
        session_key = tournament_session()["key"]

    with _lock:
        con = connect()
        row = con.execute("""
            SELECT id FROM tournament_entries
            WHERE telegram_id=? AND session_key=?
        """, (uid, session_key)).fetchone()
        con.close()

    return row is not None


def record_tournament_payment(
    uid, charge_id, provider_id, amount, currency
):
    """
    Records a successful Telegram Stars payment once and grants
    access to the active tournament session.
    """
    if currency != "XTR" or int(amount) != TOURNAMENT_STARS:
        return False

    session_key = tournament_session()["key"]

    with _lock:
        con = connect()

        try:
            con.execute("BEGIN IMMEDIATE")

            cur = con.execute("""
                INSERT OR IGNORE INTO payments
                (telegram_id, telegram_charge_id, provider_charge_id,
                 amount, currency, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                uid,
                charge_id,
                provider_id,
                int(amount),
                currency,
                datetime.now(timezone.utc).isoformat()
            ))

            inserted = cur.rowcount == 1

            if inserted:
                payment = con.execute("""
                    SELECT id FROM payments
                    WHERE telegram_charge_id=?
                """, (charge_id,)).fetchone()

                con.execute("""
                    INSERT OR IGNORE INTO tournament_entries
                    (telegram_id, session_key, payment_id, created_at)
                    VALUES (?, ?, ?, ?)
                """, (
                    uid,
                    session_key,
                    payment["id"],
                    datetime.now(timezone.utc).isoformat()
                ))

            con.commit()
            return inserted

        except Exception:
            con.rollback()
            raise

        finally:
            con.close()


def add_payment(uid, charge_id, provider_id, amount, currency):
    # Legacy payment function retained for compatibility.
    with _lock:
        con = connect()
        cur = con.execute("""
            INSERT OR IGNORE INTO payments
            (telegram_id, telegram_charge_id, provider_charge_id,
             amount, currency, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            uid,
            charge_id,
            provider_id,
            amount,
            currency,
            datetime.now(timezone.utc).isoformat()
        ))

        con.commit()
        inserted = cur.rowcount == 1
        con.close()
        return inserted


def save_score(uid, display_name, score, mode, duration):
    """Stores each completed game separately. Scores are never added."""
    session_key = tournament_session()["key"]

    with _lock:
        con = connect()
        con.execute("""
            INSERT INTO scores
            (telegram_id, display_name, score, mode,
             day_key, duration_seconds, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            uid,
            display_name,
            int(score),
            mode,
            session_key,
            int(duration),
            datetime.now(timezone.utc).isoformat()
        ))

        con.commit()
        con.close()


def list_top_scores(limit=10, session_key=None):
    if session_key is None:
        session_key = tournament_session()["key"]

    with _lock:
        con = connect()
        rows = con.execute("""
            SELECT
                telegram_id,
                MAX(display_name) AS display_name,
                MAX(score) AS score
            FROM scores
            WHERE day_key=? AND mode='tournament'
            GROUP BY telegram_id
            ORDER BY score DESC, telegram_id ASC
            LIMIT ?
        """, (session_key, int(limit))).fetchall()

        con.close()
        return rows


def get_player_ranking(uid, session_key=None):
    if session_key is None:
        session_key = tournament_session()["key"]

    with _lock:
        con = connect()

        rows = con.execute("""
            SELECT
                telegram_id,
                MAX(display_name) AS display_name,
                MAX(score) AS score
            FROM scores
            WHERE day_key=? AND mode='tournament'
            GROUP BY telegram_id
            ORDER BY score DESC, telegram_id ASC
        """, (session_key,)).fetchall()

        con.close()

    for position, row in enumerate(rows, start=1):
        if int(row["telegram_id"]) == int(uid):
            return {
                "position": position,
                "score": int(row["score"]),
                "display_name": row["display_name"]
            }

    return {
        "position": None,
        "score": 0,
        "display_name": None
    }


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


# Legacy Premium functions retained temporarily so the old bot
# does not crash before its replacement is installed.

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


def get_premium_price(default=TOURNAMENT_STARS):
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

        entries = con.execute("""
            SELECT COUNT(*) AS n FROM tournament_entries
            WHERE session_key=?
        """, (tournament_session()["key"],)).fetchone()["n"]

        con.close()

    return {
        "players": players,
        "premium": premium,
        "payments": payments,
        "stars": stars,
        "tournament_entries": entries
    }
