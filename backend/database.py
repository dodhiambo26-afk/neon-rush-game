
import os
import sqlite3
import threading

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


# ============================================================
# CONFIGURATION
# ============================================================

DB_PATH = os.getenv("DATABASE_PATH", "tap_tap_rush.db")
KENYA = ZoneInfo("Africa/Nairobi")
_lock = threading.RLock()

MONTHLY_STARS = 300
LEGACY_TOURNAMENT_STARS = 8


# ============================================================
# DATABASE CONNECTION
# ============================================================

def connect():
    con = sqlite3.connect(DB_PATH, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def utc_now():
    return datetime.now(timezone.utc)


def utc_iso():
    return utc_now().isoformat()


# ============================================================
# TOURNAMENT PERIODS
# ============================================================

def weekly_session():
    """
    Weekly tournament:
    Sunday 6:00 PM to the following Sunday 6:00 PM.
    Kenya time.
    """

    now = datetime.now(KENYA)

    days_since_sunday = (now.weekday() + 1) % 7

    start = now.replace(
        hour=18,
        minute=0,
        second=0,
        microsecond=0
    ) - timedelta(days=days_since_sunday)

    if now < start:
        start -= timedelta(days=7)

    end = start + timedelta(days=7)

    return {
        "key": start.strftime("%Y-%m-%d"),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "starts_at": "Sunday 6:00 PM",
        "ends_at": "Sunday 6:00 PM",
        "timezone": "Africa/Nairobi"
    }


def monthly_session():
    """
    Monthly tournament runs from the first day of the month
    until the first day of the following month.
    """

    now = datetime.now(KENYA)

    start = now.replace(
        day=1,
        hour=0,
        minute=0,
        second=0,
        microsecond=0
    )

    if start.month == 12:
        end = start.replace(
            year=start.year + 1,
            month=1
        )
    else:
        end = start.replace(month=start.month + 1)

    return {
        "key": start.strftime("%Y-%m"),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "starts_at": start.strftime("%d %B %Y"),
        "ends_at": (end - timedelta(seconds=1)).strftime(
            "%d %B %Y, 11:59 PM"
        ),
        "timezone": "Africa/Nairobi"
    }


# Legacy daily session retained for compatibility.
def tournament_session():
    now = datetime.now(KENYA)

    start = now.replace(
        hour=6,
        minute=0,
        second=0,
        microsecond=0
    )

    if now < start:
        start -= timedelta(days=1)

    end = start + timedelta(days=1)

    return {
        "key": start.strftime("%Y-%m-%d"),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "starts_at": "06:00 AM",
        "ends_at": "06:00 AM",
        "timezone": "Africa/Nairobi"
    }


def kenya_day_key():
    return tournament_session()["key"]


# ============================================================
# DATABASE INITIALIZATION AND SAFE MIGRATION
# ============================================================

def init_db():
    with _lock:
        con = connect()

        try:
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


            -- WEEKLY TOURNAMENT ADMISSIONS

            CREATE TABLE IF NOT EXISTS weekly_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL,
                session_key TEXT NOT NULL,
                admitted_by INTEGER,
                created_at TEXT NOT NULL,
                UNIQUE(telegram_id, session_key)
            );

            CREATE INDEX IF NOT EXISTS idx_weekly_entries
            ON weekly_entries(session_key, telegram_id);


            -- MONTHLY PREMIUM MEMBERSHIPS

            CREATE TABLE IF NOT EXISTS monthly_memberships (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL,
                month_key TEXT NOT NULL,
                payment_id INTEGER,
                starts_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(telegram_id, month_key)
            );

            CREATE INDEX IF NOT EXISTS idx_monthly_memberships
            ON monthly_memberships(month_key, telegram_id);


            -- WEEKLY ADMIN FUNDING

            CREATE TABLE IF NOT EXISTS weekly_prize_funding (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_key TEXT NOT NULL,
                admin_id INTEGER NOT NULL,
                amount REAL NOT NULL,
                currency TEXT NOT NULL,
                funding_source TEXT NOT NULL,
                reference TEXT,
                notes TEXT,
                created_at TEXT NOT NULL
            );


            -- PRIZE ALLOCATIONS AND WINNER RECORDS

            CREATE TABLE IF NOT EXISTS prize_awards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                period_type TEXT NOT NULL,
                period_key TEXT NOT NULL,
                position INTEGER NOT NULL,
                telegram_id INTEGER NOT NULL,
                amount REAL NOT NULL,
                currency TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                payment_reference TEXT,
                paid_at TEXT,
                notes TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(period_type, period_key, position)
            );

            CREATE INDEX IF NOT EXISTS idx_prize_awards_period
            ON prize_awards(period_type, period_key);


            -- ADMIN AUDIT LOG

            CREATE TABLE IF NOT EXISTS admin_activity (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                target_id INTEGER,
                details TEXT,
                created_at TEXT NOT NULL
            );
            """)

            # New installations start with weekly registration locked.
            con.execute("""
                INSERT OR IGNORE INTO settings(key, value)
                VALUES('weekly_locked', '1')
            """)

            con.execute("""
                INSERT OR IGNORE INTO settings(key, value)
                VALUES('premium_price', '300')
            """)

            con.execute("""
                INSERT OR IGNORE INTO settings(key, value)
                VALUES('monthly_prize_percentage', '30')
            """)

            con.execute("""
                INSERT OR IGNORE INTO settings(key, value)
                VALUES('monthly_retained_percentage', '70')
            """)

            con.execute("""
                INSERT OR IGNORE INTO settings(key, value)
                VALUES('support_whatsapp', '+254183375330')
            """)

            con.execute("""
                INSERT OR IGNORE INTO settings(key, value)
                VALUES('support_email', 'amimod94@yahoo.com')
            """)

            con.commit()

        finally:
            con.close()


# ============================================================
# SETTINGS
# ============================================================

def get_setting(key, default=None):
    with _lock:
        con = connect()

        try:
            row = con.execute(
                "SELECT value FROM settings WHERE key=?",
                (key,)
            ).fetchone()

            return row["value"] if row else default

        finally:
            con.close()


def set_setting(key, value):
    with _lock:
        con = connect()

        try:
            con.execute("""
                INSERT INTO settings(key, value)
                VALUES(?, ?)
                ON CONFLICT(key)
                DO UPDATE SET value=excluded.value
            """, (str(key), str(value)))

            con.commit()

        finally:
            con.close()


def is_weekly_locked():
    return get_setting("weekly_locked", "1") == "1"


def set_weekly_locked(locked):
    set_setting(
        "weekly_locked",
        "1" if locked else "0"
    )


# ============================================================
# USERS
# ============================================================

def upsert_user(uid, username=None, first_name=None, last_name=None):
    with _lock:
        con = connect()

        try:
            con.execute("""
                INSERT INTO users
                (telegram_id, username, first_name, last_name, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(telegram_id) DO UPDATE SET
                    username=excluded.username,
                    first_name=excluded.first_name,
                    last_name=excluded.last_name
            """, (
                int(uid),
                username,
                first_name,
                last_name,
                utc_iso()
            ))

            con.commit()

        finally:
            con.close()


def get_user(uid):
    with _lock:
        con = connect()

        try:
            return con.execute(
                "SELECT * FROM users WHERE telegram_id=?",
                (int(uid),)
            ).fetchone()

        finally:
            con.close()


def is_banned(uid):
    row = get_user(uid)
    return bool(row and row["banned"])


def set_banned(uid, value):
    with _lock:
        con = connect()

        try:
            con.execute(
                "UPDATE users SET banned=? WHERE telegram_id=?",
                (1 if value else 0, int(uid))
            )

            con.commit()

        finally:
            con.close()


# ============================================================
# WEEKLY TOURNAMENT ACCESS
# ============================================================

def grant_weekly_entry(uid, session_key=None, admin_id=None):
    if session_key is None:
        session_key = weekly_session()["key"]

    with _lock:
        con = connect()

        try:
            con.execute("""
                INSERT OR IGNORE INTO weekly_entries
                (telegram_id, session_key, admitted_by, created_at)
                VALUES (?, ?, ?, ?)
            """, (
                int(uid),
                session_key,
                admin_id,
                utc_iso()
            ))

            con.commit()

            row = con.execute("""
                SELECT id FROM weekly_entries
                WHERE telegram_id=? AND session_key=?
            """, (int(uid), session_key)).fetchone()

            return row is not None

        finally:
            con.close()


def revoke_weekly_entry(uid, session_key=None):
    if session_key is None:
        session_key = weekly_session()["key"]

    with _lock:
        con = connect()

        try:
            cursor = con.execute("""
                DELETE FROM weekly_entries
                WHERE telegram_id=? AND session_key=?
            """, (int(uid), session_key))

            con.commit()
            return cursor.rowcount > 0

        finally:
            con.close()


def has_weekly_entry(uid, session_key=None):
    if session_key is None:
        session_key = weekly_session()["key"]

    with _lock:
        con = connect()

        try:
            row = con.execute("""
                SELECT id FROM weekly_entries
                WHERE telegram_id=? AND session_key=?
            """, (int(uid), session_key)).fetchone()

            return row is not None

        finally:
            con.close()


def list_weekly_entries(session_key=None):
    if session_key is None:
        session_key = weekly_session()["key"]

    with _lock:
        con = connect()

        try:
            return con.execute("""
                SELECT
                    e.telegram_id,
                    e.session_key,
                    e.admitted_by,
                    e.created_at,
                    u.username,
                    u.first_name,
                    u.last_name
                FROM weekly_entries e
                LEFT JOIN users u
                    ON u.telegram_id=e.telegram_id
                WHERE e.session_key=?
                ORDER BY e.created_at DESC
            """, (session_key,)).fetchall()

        finally:
            con.close()


# ============================================================
# MONTHLY PREMIUM ACCESS
# ============================================================

def monthly_membership_expiry():
    session = monthly_session()
    end = datetime.fromisoformat(session["end"])
    return end.isoformat()


def has_monthly_access(uid, month_key=None):
    if month_key is None:
        month_key = monthly_session()["key"]

    now = utc_iso()

    with _lock:
        con = connect()

        try:
            row = con.execute("""
                SELECT id
                FROM monthly_memberships
                WHERE telegram_id=?
                AND month_key=?
                AND expires_at>?
            """, (int(uid), month_key, now)).fetchone()

            return row is not None

        finally:
            con.close()


def record_monthly_payment(
    uid,
    charge_id,
    provider_id,
    amount,
    currency
):
    """
    Records a successful 300-Star monthly membership payment.

    Membership runs until the end of the current Kenya
    calendar month. Duplicate Telegram charge IDs are rejected.
    """

    if currency != "XTR" or int(amount) != MONTHLY_STARS:
        return False

    session = monthly_session()
    now = datetime.now(KENYA)

    starts_at = session["start"]
    expires_at = session["end"]

    with _lock:
        con = connect()

        try:
            con.execute("BEGIN IMMEDIATE")

            cur = con.execute("""
                INSERT OR IGNORE INTO payments
                (
                    telegram_id,
                    telegram_charge_id,
                    provider_charge_id,
                    amount,
                    currency,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                int(uid),
                charge_id,
                provider_id,
                int(amount),
                currency,
                utc_iso()
            ))

            inserted = cur.rowcount == 1

            if not inserted:
                con.rollback()
                return False

            payment = con.execute("""
                SELECT id FROM payments
                WHERE telegram_charge_id=?
            """, (charge_id,)).fetchone()

            con.execute("""
                INSERT INTO monthly_memberships
                (
                    telegram_id,
                    month_key,
                    payment_id,
                    starts_at,
                    expires_at,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(telegram_id, month_key)
                DO UPDATE SET
                    payment_id=excluded.payment_id,
                    expires_at=excluded.expires_at
            """, (
                int(uid),
                session["key"],
                payment["id"],
                starts_at,
                expires_at,
                utc_iso()
            ))

            con.execute("""
                UPDATE users
                SET premium_until=?
                WHERE telegram_id=?
            """, (
                expires_at,
                int(uid)
            ))

            con.commit()
            return True

        except Exception:
            con.rollback()
            raise

        finally:
            con.close()


def list_monthly_members(session_key=None):
    if session_key is None:
        session_key = monthly_session()["key"]

    with _lock:
        con = connect()

        try:
            return con.execute("""
                SELECT
                    m.telegram_id,
                    m.month_key,
                    m.starts_at,
                    m.expires_at,
                    m.created_at,
                    u.username,
                    u.first_name,
                    u.last_name
                FROM monthly_memberships m
                LEFT JOIN users u
                    ON u.telegram_id=m.telegram_id
                WHERE m.month_key=?
                ORDER BY m.created_at DESC
            """, (session_key,)).fetchall()

        finally:
            con.close()


def revoke_monthly_access(uid, month_key=None):
    if month_key is None:
        month_key = monthly_session()["key"]

    with _lock:
        con = connect()

        try:
            cursor = con.execute("""
                DELETE FROM monthly_memberships
                WHERE telegram_id=? AND month_key=?
            """, (int(uid), month_key))

            con.execute("""
                UPDATE users
                SET premium_until=NULL
                WHERE telegram_id=?
            """, (int(uid),))

            con.commit()
            return cursor.rowcount > 0

        finally:
            con.close()


# ============================================================
# SCORES
# ============================================================

def score_period(mode):
    if mode == "weekly":
        return weekly_session()["key"]

    if mode == "monthly":
        return monthly_session()["key"]

    if mode == "tournament":
        return tournament_session()["key"]

    return kenya_day_key()


def save_score(uid, display_name, score, mode, duration):
    """
    Free Mode scores are not stored in the global database.
    Weekly and monthly tournament scores are stored separately.
    """

    mode = str(mode).lower().strip()

    if mode == "free":
        return False

    if mode not in ("weekly", "monthly", "tournament"):
        raise ValueError("Invalid score mode.")

    session_key = score_period(mode)

    with _lock:
        con = connect()

        try:
            con.execute("""
                INSERT INTO scores
                (
                    telegram_id,
                    display_name,
                    score,
                    mode,
                    day_key,
                    duration_seconds,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                int(uid),
                str(display_name),
                int(score),
                mode,
                session_key,
                int(duration),
                utc_iso()
            ))

            con.commit()
            return True

        finally:
            con.close()


def list_tournament_scores(
    mode,
    session_key=None,
    limit=10
):
    if mode not in ("weekly", "monthly", "tournament"):
        raise ValueError("Invalid tournament mode.")

    if session_key is None:
        session_key = score_period(mode)

    with _lock:
        con = connect()

        try:
            return con.execute("""
                SELECT
                    telegram_id,
                    MAX(display_name) AS display_name,
                    MAX(score) AS score,
                    COUNT(*) AS games_played
                FROM scores
                WHERE day_key=? AND mode=?
                GROUP BY telegram_id
                ORDER BY score DESC, telegram_id ASC
                LIMIT ?
            """, (
                session_key,
                mode,
                int(limit)
            )).fetchall()

        finally:
            con.close()


def get_tournament_ranking(
    uid,
    mode,
    session_key=None
):
    if mode not in ("weekly", "monthly", "tournament"):
        raise ValueError("Invalid tournament mode.")

    if session_key is None:
        session_key = score_period(mode)

    with _lock:
        con = connect()

        try:
            rows = con.execute("""
                SELECT
                    telegram_id,
                    MAX(display_name) AS display_name,
                    MAX(score) AS score
                FROM scores
                WHERE day_key=? AND mode=?
                GROUP BY telegram_id
                ORDER BY score DESC, telegram_id ASC
            """, (session_key, mode)).fetchall()

        finally:
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


# ============================================================
# LEGACY SCORE FUNCTIONS
# Kept for compatibility until the old bot is replaced.
# ============================================================

def list_top_scores(limit=10, session_key=None):
    return list_tournament_scores(
        "tournament",
        session_key,
        limit
    )


def get_player_ranking(uid, session_key=None):
    return get_tournament_ranking(
        uid,
        "tournament",
        session_key
    )


# ============================================================
# LEGACY DAILY TOURNAMENT COMPATIBILITY
# These functions will no longer be used by the new system.
# ============================================================

def grant_tournament_entry(uid, session_key=None, payment_id=None):
    if session_key is None:
        session_key = tournament_session()["key"]

    with _lock:
        con = connect()

        try:
            con.execute("""
                INSERT OR IGNORE INTO tournament_entries
                (telegram_id, session_key, payment_id, created_at)
                VALUES (?, ?, ?, ?)
            """, (
                int(uid),
                session_key,
                payment_id,
                utc_iso()
            ))

            con.commit()

            row = con.execute("""
                SELECT id FROM tournament_entries
                WHERE telegram_id=? AND session_key=?
            """, (int(uid), session_key)).fetchone()

            return row is not None

        finally:
            con.close()


def has_tournament_entry(uid, session_key=None):
    if session_key is None:
        session_key = tournament_session()["key"]

    with _lock:
        con = connect()

        try:
            row = con.execute("""
                SELECT id FROM tournament_entries
                WHERE telegram_id=? AND session_key=?
            """, (int(uid), session_key)).fetchone()

            return row is not None

        finally:
            con.close()


def record_tournament_payment(
    uid,
    charge_id,
    provider_id,
    amount,
    currency
):
    if currency != "XTR" or int(amount) != LEGACY_TOURNAMENT_STARS:
        return False

    session_key = tournament_session()["key"]

    with _lock:
        con = connect()

        try:
            con.execute("BEGIN IMMEDIATE")

            cur = con.execute("""
                INSERT OR IGNORE INTO payments
                (
                    telegram_id,
                    telegram_charge_id,
                    provider_charge_id,
                    amount,
                    currency,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                int(uid),
                charge_id,
                provider_id,
                int(amount),
                currency,
                utc_iso()
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
                    int(uid),
                    session_key,
                    payment["id"],
                    utc_iso()
                ))

            con.commit()
            return inserted

        except Exception:
            con.rollback()
            raise

        finally:
            con.close()


def add_payment(uid, charge_id, provider_id, amount, currency):
    with _lock:
        con = connect()

        try:
            cur = con.execute("""
                INSERT OR IGNORE INTO payments
                (
                    telegram_id,
                    telegram_charge_id,
                    provider_charge_id,
                    amount,
                    currency,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                int(uid),
                charge_id,
                provider_id,
                int(amount),
                currency,
                utc_iso()
            ))

            con.commit()
            return cur.rowcount == 1

        finally:
            con.close()


# ============================================================
# LEGACY PREMIUM COMPATIBILITY
# ============================================================

def set_premium(uid, days):
    now = utc_now()
    row = get_user(uid)

    current = None

    if row and row["premium_until"]:
        try:
            current = datetime.fromisoformat(row["premium_until"])
        except ValueError:
            current = None

    start = current if current and current > now else now
    expiry = (start + timedelta(days=int(days))).isoformat()

    with _lock:
        con = connect()

        try:
            con.execute(
                "UPDATE users SET premium_until=? WHERE telegram_id=?",
                (expiry, int(uid))
            )

            con.commit()

        finally:
            con.close()

    return expiry


def revoke_premium(uid):
    with _lock:
        con = connect()

        try:
            con.execute(
                "UPDATE users SET premium_until=NULL WHERE telegram_id=?",
                (int(uid),)
            )

            con.commit()

        finally:
            con.close()


def set_premium_price(stars):
    set_setting("premium_price", int(stars))


def get_premium_price(default=MONTHLY_STARS):
    try:
        return int(get_setting("premium_price", default))
    except (ValueError, TypeError):
        return default


# ============================================================
# WEEKLY PRIZE FUNDING
# ============================================================

def add_weekly_funding(
    session_key,
    admin_id,
    amount,
    currency="KES",
    funding_source="Administrator",
    reference=None,
    notes=None
):
    amount = float(amount)

    if amount <= 0:
        raise ValueError("Funding amount must be greater than zero.")

    with _lock:
        con = connect()

        try:
            cur = con.execute("""
                INSERT INTO weekly_prize_funding
                (
                    session_key,
                    admin_id,
                    amount,
                    currency,
                    funding_source,
                    reference,
                    notes,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                session_key,
                int(admin_id),
                amount,
                currency,
                funding_source,
                reference,
                notes,
                utc_iso()
            ))

            con.commit()
            return cur.lastrowid

        finally:
            con.close()


def list_weekly_funding(session_key=None):
    if session_key is None:
        session_key = weekly_session()["key"]

    with _lock:
        con = connect()

        try:
            return con.execute("""
                SELECT *
                FROM weekly_prize_funding
                WHERE session_key=?
                ORDER BY created_at DESC
            """, (session_key,)).fetchall()

        finally:
            con.close()


# ============================================================
# TOP 5 PRIZE AWARDS AND PAYOUT RECORDS
# ============================================================

def save_prize_award(
    period_type,
    period_key,
    position,
    telegram_id,
    amount,
    currency="KES",
    status="pending",
    notes=None
):
    if period_type not in ("weekly", "monthly"):
        raise ValueError("Invalid prize period.")

    if int(position) < 1 or int(position) > 5:
        raise ValueError("Only the Top 5 positions can receive awards.")

    if float(amount) < 0:
        raise ValueError("Prize amount cannot be negative.")

    with _lock:
        con = connect()

        try:
            cur = con.execute("""
                INSERT INTO prize_awards
                (
                    period_type,
                    period_key,
                    position,
                    telegram_id,
                    amount,
                    currency,
                    status,
                    notes,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(period_type, period_key, position)
                DO UPDATE SET
                    telegram_id=excluded.telegram_id,
                    amount=excluded.amount,
                    currency=excluded.currency,
                    status=excluded.status,
                    notes=excluded.notes
            """, (
                period_type,
                period_key,
                int(position),
                int(telegram_id),
                float(amount),
                currency,
                status,
                notes,
                utc_iso()
            ))

            con.commit()
            return cur.lastrowid

        finally:
            con.close()


def mark_prize_paid(
    award_id,
    payment_reference=None,
    notes=None
):
    with _lock:
        con = connect()

        try:
            cur = con.execute("""
                UPDATE prize_awards
                SET status='paid',
                    payment_reference=?,
                    paid_at=?,
                    notes=COALESCE(?, notes)
                WHERE id=?
            """, (
                payment_reference,
                utc_iso(),
                notes,
                int(award_id)
            ))

            con.commit()
            return cur.rowcount > 0

        finally:
            con.close()


def list_prize_awards(period_type=None, period_key=None):
    sql = "SELECT * FROM prize_awards WHERE 1=1"
    params = []

    if period_type:
        sql += " AND period_type=?"
        params.append(period_type)

    if period_key:
        sql += " AND period_key=?"
        params.append(period_key)

    sql += " ORDER BY period_type, period_key DESC, position ASC"

    with _lock:
        con = connect()

        try:
            return con.execute(sql, params).fetchall()

        finally:
            con.close()


# ============================================================
# GENERAL DATABASE STATISTICS
# ============================================================

def get_stats():
    with _lock:
        con = connect()

        try:
            players = con.execute(
                "SELECT COUNT(*) AS n FROM users"
            ).fetchone()["n"]

            now = utc_iso()

            premium = con.execute("""
                SELECT COUNT(*) AS n
                FROM users
                WHERE premium_until>?
            """, (now,)).fetchone()["n"]

            payments = con.execute(
                "SELECT COUNT(*) AS n FROM payments"
            ).fetchone()["n"]

            stars = con.execute("""
                SELECT COALESCE(SUM(amount), 0) AS n
                FROM payments
                WHERE currency='XTR'
            """).fetchone()["n"]

            weekly_entries = con.execute("""
                SELECT COUNT(*) AS n
                FROM weekly_entries
                WHERE session_key=?
            """, (
                weekly_session()["key"],
            )).fetchone()["n"]

            monthly_members = con.execute("""
                SELECT COUNT(*) AS n
                FROM monthly_memberships
                WHERE month_key=?
                AND expires_at>?
            """, (
                monthly_session()["key"],
                now
            )).fetchone()["n"]

            legacy_entries = con.execute("""
                SELECT COUNT(*) AS n
                FROM tournament_entries
                WHERE session_key=?
            """, (
                tournament_session()["key"],
            )).fetchone()["n"]

            return {
                "players": players,
                "premium": premium,
                "payments": payments,
                "stars": stars,
                "weekly_entries": weekly_entries,
                "monthly_members": monthly_members,
                "tournament_entries": legacy_entries
            }

        finally:
            con.close()
