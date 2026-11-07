
import os
import hmac
import json
import hashlib
import asyncio

from datetime import datetime, timezone
from urllib.parse import parse_qsl
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Header, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

import database as db
from bot import run_bot


# ============================================================
# CONFIGURATION
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
MAX_AUTH_AGE = 86400

MONTHLY_STARS = 300
MONTHLY_PRIZE_PERCENTAGE = 30
MONTHLY_RETAINED_PERCENTAGE = 70

SUPPORT_WHATSAPP = "+254183375330"
SUPPORT_EMAIL = "amimod94@yahoo.com"


# ============================================================
# TELEGRAM AUTHENTICATION
# ============================================================

def verify_telegram_data(init_data):
    if not BOT_TOKEN or not init_data:
        return None

    try:
        data = dict(parse_qsl(init_data, keep_blank_values=True))

        received_hash = data.pop("hash", None)

        if not received_hash:
            return None

        auth_date = int(data.get("auth_date", "0"))
        now = int(datetime.now(timezone.utc).timestamp())

        if auth_date > now + 60:
            return None

        if now - auth_date > MAX_AUTH_AGE:
            return None

        data_check_string = "\n".join(
            f"{key}={value}"
            for key, value in sorted(data.items())
        )

        secret_key = hmac.new(
            b"WebAppData",
            BOT_TOKEN.encode(),
            hashlib.sha256
        ).digest()

        calculated_hash = hmac.new(
            secret_key,
            data_check_string.encode(),
            hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(
            calculated_hash,
            received_hash
        ):
            return None

        user_data = json.loads(data.get("user", "{}"))

        if not user_data.get("id"):
            return None

        return user_data

    except (ValueError, TypeError, json.JSONDecodeError):
        return None


def authenticate(init_data):
    user = verify_telegram_data(init_data)

    if not user:
        raise HTTPException(
            status_code=401,
            detail="Telegram authentication failed. Open the game through Telegram."
        )

    uid = int(user["id"])

    db.upsert_user(
        uid,
        user.get("username"),
        user.get("first_name"),
        user.get("last_name")
    )

    if db.is_banned(uid):
        raise HTTPException(
            status_code=403,
            detail="Your account has been blocked."
        )

    return user


def display_name(user):
    name = " ".join(
        part for part in [
            user.get("first_name"),
            user.get("last_name")
        ] if part
    )

    if name:
        return name

    if user.get("username"):
        return "@" + user["username"]

    return "Player " + str(user["id"])


# ============================================================
# ADMIN SECURITY
# ============================================================

def get_admin_ids():
    raw = ",".join([
        os.getenv("ADMIN_ID", ""),
        os.getenv("ADMIN_IDS", "")
    ])

    return {
        int(value.strip())
        for value in raw.split(",")
        if value.strip().isdigit()
    }


def require_admin(init_data):
    user = verify_telegram_data(init_data)

    if not user:
        raise HTTPException(
            status_code=401,
            detail="Telegram verification failed. Open this page through your Telegram bot."
        )

    uid = int(user["id"])

    if uid not in get_admin_ids():
        raise HTTPException(
            status_code=403,
            detail="Access denied. This Telegram account is not an administrator."
        )

    return user


# ============================================================
# DATABASE HELPERS
# ============================================================

def admin_connection():
    return db.connect()


def admin_rows(sql, params=()):
    con = admin_connection()

    try:
        return [
            dict(row)
            for row in con.execute(sql, params).fetchall()
        ]

    finally:
        con.close()


def admin_one(sql, params=()):
    con = admin_connection()

    try:
        row = con.execute(sql, params).fetchone()
        return dict(row) if row else None

    finally:
        con.close()


def record_admin_action(
    admin_id,
    action,
    target_id=None,
    details=""
):
    con = admin_connection()

    try:
        con.execute("""
            INSERT INTO admin_activity
            (admin_id, action, target_id, details, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (
            int(admin_id),
            action,
            target_id,
            details,
            datetime.now(timezone.utc).isoformat()
        ))

        con.commit()

    finally:
        con.close()


def get_monthly_financial_summary():
    session = db.monthly_session()

    start = datetime.fromisoformat(
        session["start"]
    ).astimezone(timezone.utc).isoformat()

    end = datetime.fromisoformat(
        session["end"]
    ).astimezone(timezone.utc).isoformat()

    payment = admin_one("""
        SELECT COALESCE(SUM(amount), 0) AS total
        FROM payments
        WHERE currency='XTR'
        AND amount=?
        AND created_at>=?
        AND created_at<?
    """, (
        MONTHLY_STARS,
        start,
        end
    ))

    collected = int(payment["total"] or 0)

    prize_pool = collected * MONTHLY_PRIZE_PERCENTAGE / 100
    retained = collected * MONTHLY_RETAINED_PERCENTAGE / 100

    return {
        "month": session["key"],
        "stars_collected": collected,
        "prize_percentage": MONTHLY_PRIZE_PERCENTAGE,
        "retained_percentage": MONTHLY_RETAINED_PERCENTAGE,
        "prize_pool_stars": prize_pool,
        "retained_stars": retained
    }


# ============================================================
# APPLICATION STARTUP
# ============================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()

    bot_task = asyncio.create_task(run_bot())

    try:
        yield

    finally:
        bot_task.cancel()

        try:
            await bot_task
        except asyncio.CancelledError:
            pass


app = FastAPI(
    title="NEON RUSH Game Backend",
    version="2.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://dodhiambo26-afk.github.io"
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=[
        "Content-Type",
        "X-Telegram-Init-Data"
    ]
)


# ============================================================
# REQUEST MODELS
# ============================================================

class ScoreSubmission(BaseModel):
    score: int = Field(ge=0, le=100000)
    mode: str
    duration: int = Field(default=0, ge=0, le=3600)


class AdminPlayerAction(BaseModel):
    telegram_id: int = Field(gt=0)


class WeeklyLockAction(BaseModel):
    locked: bool


class WeeklyFundingAction(BaseModel):
    amount: float = Field(gt=0, le=1000000000)
    currency: str = Field(default="KES", max_length=10)
    funding_source: str = Field(default="Administrator", max_length=100)
    reference: str | None = Field(default=None, max_length=200)
    notes: str | None = Field(default=None, max_length=1000)


class PrizeAwardAction(BaseModel):
    period_type: str
    period_key: str
    position: int = Field(ge=1, le=5)
    telegram_id: int = Field(gt=0)
    amount: float = Field(ge=0)
    currency: str = Field(default="KES", max_length=10)
    notes: str | None = Field(default=None, max_length=1000)


class PrizePaidAction(BaseModel):
    payment_reference: str | None = Field(
        default=None,
        max_length=200
    )
    notes: str | None = Field(default=None, max_length=1000)


class MonthlyGrantAction(BaseModel):
    telegram_id: int = Field(gt=0)


class ContactSettingsAction(BaseModel):
    whatsapp: str = Field(max_length=40)
    email: str = Field(max_length=200)


# ============================================================
# PUBLIC ROUTES
# ============================================================

@app.get("/")
def home():
    return {
        "status": "online",
        "game": "NEON RUSH",
        "version": "2.0",
        "weekly_tournament": {
            "schedule": "Sunday 6:00 PM to Sunday 6:00 PM",
            "timezone": "Africa/Nairobi",
            "entry_price": 0,
            "locked": db.is_weekly_locked()
        },
        "monthly_tournament": {
            "price_stars": MONTHLY_STARS,
            "expiry": "End of calendar month"
        }
    }


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/api/contact")
def public_contact():
    return {
        "whatsapp": db.get_setting(
            "support_whatsapp",
            SUPPORT_WHATSAPP
        ),
        "email": db.get_setting(
            "support_email",
            SUPPORT_EMAIL
        )
    }


# ============================================================
# TELEGRAM PLAYER AUTHENTICATION
# UPDATED: RETURNS FLAT AND NESTED ACCESS FIELDS
# ============================================================

@app.post("/api/auth/telegram")
def telegram_auth(
    x_telegram_init_data: str = Header(
        default="",
        alias="X-Telegram-Init-Data"
    )
):
    user = authenticate(x_telegram_init_data)
    uid = int(user["id"])

    weekly = db.weekly_session()
    monthly = db.monthly_session()

    weekly_access = db.has_weekly_entry(
        uid,
        weekly["key"]
    )

    monthly_access = db.has_monthly_access(
        uid,
        monthly["key"]
    )

    weekly_locked = db.is_weekly_locked()

    return {
        "authenticated": True,

        "user": {
            "id": uid,
            "username": user.get("username"),
            "first_name": user.get("first_name"),
            "last_name": user.get("last_name"),
            "display_name": display_name(user)
        },

        # Flat fields required by the current game
        "weekly_access": weekly_access,
        "weekly_locked": weekly_locked,
        "weekly_session": weekly,

        "monthly_access": monthly_access,
        "monthly_session": monthly,
        "monthly_expiry": monthly["end"],

        # Nested fields retained for compatibility
        "weekly": {
            "session": weekly,
            "has_access": weekly_access,
            "locked": weekly_locked,
            "entry_price": 0
        },

        "monthly": {
            "session": monthly,
            "has_access": monthly_access,
            "entry_price": MONTHLY_STARS,
            "expires_at": monthly["end"]
        },

        "premium": monthly_access,

        "contact": {
            "whatsapp": db.get_setting(
                "support_whatsapp",
                SUPPORT_WHATSAPP
            ),
            "email": db.get_setting(
                "support_email",
                SUPPORT_EMAIL
            )
        }
    }


# ============================================================
# GAME SCORE SUBMISSION
# ============================================================

@app.post("/api/scores")
def submit_score(
    submission: ScoreSubmission,
    x_telegram_init_data: str = Header(
        default="",
        alias="X-Telegram-Init-Data"
    )
):
    user = authenticate(x_telegram_init_data)
    uid = int(user["id"])

    mode = submission.mode.lower().strip()

    if mode == "free":
        return {
            "saved": False,
            "score": submission.score,
            "mode": "free",
            "leaderboard": None,
            "player": None
        }

    if mode not in ("weekly", "monthly"):
        raise HTTPException(
            status_code=400,
            detail="Invalid game mode. Use free, weekly or monthly."
        )

    if mode == "weekly":
        session = db.weekly_session()

        if not db.has_weekly_entry(uid, session["key"]):
            raise HTTPException(
                status_code=403,
                detail="You have not been admitted to this week's tournament."
            )

    else:
        session = db.monthly_session()

        if not db.has_monthly_access(uid, session["key"]):
            raise HTTPException(
                status_code=403,
                detail="Your monthly membership is not active. Purchase monthly access through the Telegram bot."
            )

    db.save_score(
        uid,
        display_name(user),
        submission.score,
        mode,
        submission.duration
    )

    top_rows = db.list_tournament_scores(
        mode=mode,
        session_key=session["key"],
        limit=10
    )

    leaderboard_data = [
        {
            "position": index + 1,
            "telegram_id": int(row["telegram_id"]),
            "name": row["display_name"],
            "score": int(row["score"])
        }
        for index, row in enumerate(top_rows)
    ]

    player = db.get_tournament_ranking(
        uid,
        mode,
        session["key"]
    )

    return {
        "saved": True,
        "score": submission.score,
        "mode": mode,
        "leaderboard": leaderboard_data,
        "player": player,
        "tournament": session
    }


# ============================================================
# PUBLIC WEEKLY AND MONTHLY LEADERBOARDS
# ============================================================

@app.get("/api/leaderboard")
def leaderboard(
    mode: str = Query(default="weekly"),
    x_telegram_init_data: str = Header(
        default="",
        alias="X-Telegram-Init-Data"
    )
):
    user = authenticate(x_telegram_init_data)
    uid = int(user["id"])

    mode = mode.lower().strip()

    if mode == "weekly":
        session = db.weekly_session()
        has_access = db.has_weekly_entry(uid, session["key"])

    elif mode == "monthly":
        session = db.monthly_session()
        has_access = db.has_monthly_access(uid, session["key"])

    else:
        raise HTTPException(
            status_code=400,
            detail="Leaderboard mode must be weekly or monthly."
        )

    rows = db.list_tournament_scores(
        mode,
        session["key"],
        10
    )

    data = [
        {
            "position": index + 1,
            "name": row["display_name"],
            "score": int(row["score"])
        }
        for index, row in enumerate(rows)
    ]

    player = db.get_tournament_ranking(
        uid,
        mode,
        session["key"]
    ) if has_access else {
        "position": None,
        "score": 0,
        "display_name": None
    }

    return {
        "mode": mode,
        "leaderboard": data,
        "player": player,
        "has_access": has_access,
        "tournament": session
    }


# ============================================================
# PUBLIC TOURNAMENT STATUS
# ============================================================

@app.get("/api/tournament/status")
def tournament_status(
    x_telegram_init_data: str = Header(
        default="",
        alias="X-Telegram-Init-Data"
    )
):
    user = authenticate(x_telegram_init_data)
    uid = int(user["id"])

    weekly = db.weekly_session()
    monthly = db.monthly_session()

    return {
        "weekly": {
            "session": weekly,
            "has_access": db.has_weekly_entry(
                uid,
                weekly["key"]
            ),
            "locked": db.is_weekly_locked(),
            "entry_price": 0
        },

        "monthly": {
            "session": monthly,
            "has_access": db.has_monthly_access(
                uid,
                monthly["key"]
            ),
            "entry_price": MONTHLY_STARS
        }
    }


# ============================================================
# ADMIN ACCESS CHECK
# ============================================================

@app.get("/api/admin/check")
def admin_check(
    x_telegram_init_data: str = Header(
        default="",
        alias="X-Telegram-Init-Data"
    )
):
    user = require_admin(x_telegram_init_data)

    return {
        "authorized": True,
        "admin": {
            "id": int(user["id"]),
            "name": display_name(user)
        }
    }


# ============================================================
# ADMIN OVERVIEW
# ============================================================

@app.get("/api/admin/overview")
def admin_overview(
    x_telegram_init_data: str = Header(
        default="",
        alias="X-Telegram-Init-Data"
    )
):
    admin = require_admin(x_telegram_init_data)

    stats = db.get_stats()
    weekly = db.weekly_session()
    monthly = db.monthly_session()

    monthly_finances = get_monthly_financial_summary()

    weekly_funding = admin_one("""
        SELECT COALESCE(SUM(amount), 0) AS total
        FROM weekly_prize_funding
        WHERE session_key=?
        AND currency='KES'
    """, (weekly["key"],))

    weekly_prizes = admin_one("""
        SELECT COALESCE(SUM(amount), 0) AS total
        FROM prize_awards
        WHERE period_type='weekly'
        AND period_key=?
        AND currency='KES'
    """, (weekly["key"],))

    return {
        "stats": stats,

        "players": stats["players"],
        "stars_collected": stats["stars"],

        "weekly": {
            "session": weekly,
            "locked": db.is_weekly_locked(),
            "entries": stats["weekly_entries"],
            "funding_kes": float(
                weekly_funding["total"] or 0
            ),
            "prizes_kes": float(
                weekly_prizes["total"] or 0
            )
        },

        "monthly": {
            "session": monthly,
            "members": stats["monthly_members"],
            "financials": monthly_finances
        },

        "admin_id": int(admin["id"])
    }


# ============================================================
# ADMIN PLAYER LIST AND SEARCH
# ============================================================

@app.get("/api/admin/players")
def admin_players(
    q: str = Query(default="", max_length=100),
    limit: int = Query(default=100, ge=1, le=500),
    x_telegram_init_data: str = Header(
        default="",
        alias="X-Telegram-Init-Data"
    )
):
    require_admin(x_telegram_init_data)

    search = "%" + q.strip() + "%"

    return {
        "players": admin_rows("""
            SELECT
                u.telegram_id,
                u.username,
                u.first_name,
                u.last_name,
                u.banned,
                u.created_at,
                (
                    SELECT MAX(s.score)
                    FROM scores s
                    WHERE s.telegram_id=u.telegram_id
                ) AS best_score
            FROM users u
            WHERE
                (? = '')
                OR CAST(u.telegram_id AS TEXT) LIKE ?
                OR COALESCE(u.username, '') LIKE ?
                OR COALESCE(u.first_name, '') LIKE ?
                OR COALESCE(u.last_name, '') LIKE ?
            ORDER BY u.created_at DESC
            LIMIT ?
        """, (
            q.strip(),
            search,
            search,
            search,
            search,
            limit
        ))
    }


# ============================================================
# ADMIN WEEKLY ENTRIES
# ============================================================

@app.get("/api/admin/entries")
def admin_entries(
    x_telegram_init_data: str = Header(
        default="",
        alias="X-Telegram-Init-Data"
    )
):
    require_admin(x_telegram_init_data)

    session = db.weekly_session()

    return {
        "session": session,
        "locked": db.is_weekly_locked(),
        "entries": [
            dict(row)
            for row in db.list_weekly_entries(session["key"])
        ]
    }


# ============================================================
# ADMIN WEEKLY GRANT
# ============================================================

@app.post("/api/admin/grant")
def admin_grant(
    action: AdminPlayerAction,
    x_telegram_init_data: str = Header(
        default="",
        alias="X-Telegram-Init-Data"
    )
):
    admin = require_admin(x_telegram_init_data)
    uid = int(action.telegram_id)

    player = admin_one(
        "SELECT telegram_id FROM users WHERE telegram_id=?",
        (uid,)
    )

    if not player:
        raise HTTPException(
            status_code=404,
            detail="Player not found. Ask the player to open the game first."
        )

    session = db.weekly_session()

    if db.has_weekly_entry(uid, session["key"]):
        raise HTTPException(
            status_code=409,
            detail="This player already has weekly tournament access."
        )

    db.grant_weekly_entry(
        uid,
        session["key"],
        int(admin["id"])
    )

    record_admin_action(
        admin["id"],
        "GRANT_WEEKLY_ENTRY",
        uid,
        "Weekly tournament " + session["key"]
    )

    return {
        "success": True,
        "message": "Weekly tournament access granted.",
        "telegram_id": uid,
        "session": session["key"]
    }


# ============================================================
# ADMIN WEEKLY REVOKE
# ============================================================

@app.post("/api/admin/revoke")
def admin_revoke(
    action: AdminPlayerAction,
    x_telegram_init_data: str = Header(
        default="",
        alias="X-Telegram-Init-Data"
    )
):
    admin = require_admin(x_telegram_init_data)
    uid = int(action.telegram_id)

    session = db.weekly_session()

    removed = db.revoke_weekly_entry(
        uid,
        session["key"]
    )

    if not removed:
        raise HTTPException(
            status_code=404,
            detail="No weekly tournament entry was found."
        )

    record_admin_action(
        admin["id"],
        "REVOKE_WEEKLY_ENTRY",
        uid,
        "Weekly tournament " + session["key"]
    )

    return {
        "success": True,
        "message": "Weekly tournament access revoked."
    }


# ============================================================
# ADMIN WEEKLY LOCK / UNLOCK
# ============================================================

@app.post("/api/admin/weekly/lock")
def admin_weekly_lock(
    action: WeeklyLockAction,
    x_telegram_init_data: str = Header(
        default="",
        alias="X-Telegram-Init-Data"
    )
):
    admin = require_admin(x_telegram_init_data)

    db.set_weekly_locked(action.locked)

    record_admin_action(
        admin["id"],
        "SET_WEEKLY_LOCK",
        None,
        "Weekly locked: " + str(action.locked)
    )

    return {
        "success": True,
        "locked": action.locked
    }


# ============================================================
# ADMIN MONTHLY MEMBERS
# ============================================================

@app.get("/api/admin/monthly/members")
def admin_monthly_members(
    x_telegram_init_data: str = Header(
        default="",
        alias="X-Telegram-Init-Data"
    )
):
    require_admin(x_telegram_init_data)

    session = db.monthly_session()

    return {
        "session": session,
        "members": [
            dict(row)
            for row in db.list_monthly_members(session["key"])
        ]
    }


# ============================================================
# ADMIN MANUAL MONTHLY ACCESS
# ============================================================

@app.post("/api/admin/monthly/grant")
def admin_monthly_grant(
    action: MonthlyGrantAction,
    x_telegram_init_data: str = Header(
        default="",
        alias="X-Telegram-Init-Data"
    )
):
    admin = require_admin(x_telegram_init_data)

    uid = int(action.telegram_id)
    user = db.get_user(uid)

    if not user:
        raise HTTPException(
            status_code=404,
            detail="Player not found."
        )

    session = db.monthly_session()

    con = db.connect()

    try:
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
            VALUES (?, ?, NULL, ?, ?, ?)
            ON CONFLICT(telegram_id, month_key)
            DO UPDATE SET
                starts_at=excluded.starts_at,
                expires_at=excluded.expires_at
        """, (
            uid,
            session["key"],
            session["start"],
            session["end"],
            datetime.now(timezone.utc).isoformat()
        ))

        con.execute("""
            UPDATE users
            SET premium_until=?
            WHERE telegram_id=?
        """, (session["end"], uid))

        con.commit()

    finally:
        con.close()

    record_admin_action(
        admin["id"],
        "GRANT_MONTHLY_ACCESS",
        uid,
        "Manual monthly access " + session["key"]
    )

    return {
        "success": True,
        "message": "Monthly access granted.",
        "expires_at": session["end"]
    }


# ============================================================
# ADMIN REVOKE MONTHLY ACCESS
# ============================================================

@app.post("/api/admin/monthly/revoke")
def admin_monthly_revoke(
    action: AdminPlayerAction,
    x_telegram_init_data: str = Header(
        default="",
        alias="X-Telegram-Init-Data"
    )
):
    admin = require_admin(x_telegram_init_data)

    uid = int(action.telegram_id)
    session = db.monthly_session()

    removed = db.revoke_monthly_access(
        uid,
        session["key"]
    )

    if not removed:
        raise HTTPException(
            status_code=404,
            detail="No monthly membership was found."
        )

    record_admin_action(
        admin["id"],
        "REVOKE_MONTHLY_ACCESS",
        uid,
        "Monthly membership " + session["key"]
    )

    return {
        "success": True,
        "message": "Monthly access revoked."
    }


# ============================================================
# ADMIN WEEKLY FUNDING
# ============================================================

@app.get("/api/admin/weekly/funding")
def admin_weekly_funding(
    x_telegram_init_data: str = Header(
        default="",
        alias="X-Telegram-Init-Data"
    )
):
    require_admin(x_telegram_init_data)

    session = db.weekly_session()

    rows = db.list_weekly_funding(session["key"])

    total_by_currency = {}

    for row in rows:
        currency = row["currency"]
        total_by_currency[currency] = (
            total_by_currency.get(currency, 0)
            + float(row["amount"])
        )

    return {
        "session": session,
        "funding": [dict(row) for row in rows],
        "totals": total_by_currency
    }


@app.post("/api/admin/weekly/funding")
def admin_add_weekly_funding(
    action: WeeklyFundingAction,
    x_telegram_init_data: str = Header(
        default="",
        alias="X-Telegram-Init-Data"
    )
):
    admin = require_admin(x_telegram_init_data)

    session = db.weekly_session()

    funding_id = db.add_weekly_funding(
        session_key=session["key"],
        admin_id=int(admin["id"]),
        amount=action.amount,
        currency=action.currency.upper(),
        funding_source=action.funding_source,
        reference=action.reference,
        notes=action.notes
    )

    record_admin_action(
        admin["id"],
        "ADD_WEEKLY_PRIZE_FUNDING",
        None,
        f"{action.amount} {action.currency}"
    )

    return {
        "success": True,
        "funding_id": funding_id
    }


# ============================================================
# ADMIN MONTHLY FINANCIAL SUMMARY
# ============================================================

@app.get("/api/admin/monthly/financials")
def admin_monthly_financials(
    x_telegram_init_data: str = Header(
        default="",
        alias="X-Telegram-Init-Data"
    )
):
    require_admin(x_telegram_init_data)

    return get_monthly_financial_summary()


# ============================================================
# ADMIN PRIZE RECORDS
# ============================================================

@app.get("/api/admin/prizes")
def admin_prizes(
    period_type: str = Query(default=""),
    period_key: str = Query(default=""),
    x_telegram_init_data: str = Header(
        default="",
        alias="X-Telegram-Init-Data"
    )
):
    require_admin(x_telegram_init_data)

    if period_type and period_type not in ("weekly", "monthly"):
        raise HTTPException(
            status_code=400,
            detail="Invalid prize period."
        )

    return {
        "awards": [
            dict(row)
            for row in db.list_prize_awards(
                period_type or None,
                period_key or None
            )
        ]
    }


@app.post("/api/admin/prizes")
def admin_create_prize(
    action: PrizeAwardAction,
    x_telegram_init_data: str = Header(
        default="",
        alias="X-Telegram-Init-Data"
    )
):
    admin = require_admin(x_telegram_init_data)

    if action.period_type not in ("weekly", "monthly"):
        raise HTTPException(
            status_code=400,
            detail="Prize period must be weekly or monthly."
        )

    player = db.get_user(action.telegram_id)

    if not player:
        raise HTTPException(
            status_code=404,
            detail="Prize winner was not found."
        )

    award_id = db.save_prize_award(
        period_type=action.period_type,
        period_key=action.period_key,
        position=action.position,
        telegram_id=action.telegram_id,
        amount=action.amount,
        currency=action.currency.upper(),
        notes=action.notes
    )

    record_admin_action(
        admin["id"],
        "CREATE_PRIZE_AWARD",
        action.telegram_id,
        f"{action.period_type} {action.period_key}, position {action.position}"
    )

    return {
        "success": True,
        "award_id": award_id
    }


@app.post("/api/admin/prizes/{award_id}/paid")
def admin_mark_prize_paid(
    award_id: int,
    action: PrizePaidAction,
    x_telegram_init_data: str = Header(
        default="",
        alias="X-Telegram-Init-Data"
    )
):
    admin = require_admin(x_telegram_init_data)

    changed = db.mark_prize_paid(
        award_id,
        action.payment_reference,
        action.notes
    )

    if not changed:
        raise HTTPException(
            status_code=404,
            detail="Prize record not found."
        )

    record_admin_action(
        admin["id"],
        "MARK_PRIZE_PAID",
        None,
        f"Prize award {award_id}"
    )

    return {
        "success": True,
        "message": "Prize payout recorded."
    }


# ============================================================
# ADMIN LEADERBOARDS AND PERFORMANCE
# ============================================================

@app.get("/api/admin/performance")
def admin_performance(
    mode: str = Query(default="weekly"),
    x_telegram_init_data: str = Header(
        default="",
        alias="X-Telegram-Init-Data"
    )
):
    require_admin(x_telegram_init_data)

    mode = mode.lower().strip()

    if mode == "weekly":
        session = db.weekly_session()

        has_access_sql = """
            EXISTS (
                SELECT 1 FROM weekly_entries e
                WHERE e.telegram_id=s.telegram_id
                AND e.session_key=s.day_key
            )
        """

    elif mode == "monthly":
        session = db.monthly_session()

        has_access_sql = """
            EXISTS (
                SELECT 1 FROM monthly_memberships m
                WHERE m.telegram_id=s.telegram_id
                AND m.month_key=s.day_key
            )
        """

    else:
        raise HTTPException(
            status_code=400,
            detail="Mode must be weekly or monthly."
        )

    rows = admin_rows(f"""
        SELECT
            s.telegram_id,
            MAX(s.display_name) AS display_name,
            MAX(s.score) AS score,
            COUNT(*) AS games_played
        FROM scores s
        WHERE s.mode=?
        AND s.day_key=?
        AND {has_access_sql}
        GROUP BY s.telegram_id
        ORDER BY score DESC, s.telegram_id ASC
        LIMIT 100
    """, (mode, session["key"]))

    return {
        "mode": mode,
        "session": session,
        "top_scores": rows,
        "top_five": rows[:5]
    }


# ============================================================
# ADMIN PAYMENT RECORDS
# ============================================================

@app.get("/api/admin/payments")
def admin_payments(
    limit: int = Query(default=100, ge=1, le=500),
    x_telegram_init_data: str = Header(
        default="",
        alias="X-Telegram-Init-Data"
    )
):
    require_admin(x_telegram_init_data)

    return {
        "payments": admin_rows("""
            SELECT
                p.id,
                p.telegram_id,
                p.telegram_charge_id,
                p.provider_charge_id,
                p.amount,
                p.currency,
                p.created_at,
                u.username,
                u.first_name,
                u.last_name
            FROM payments p
            LEFT JOIN users u
                ON u.telegram_id=p.telegram_id
            ORDER BY p.created_at DESC
            LIMIT ?
        """, (limit,))
    }


# ============================================================
# ADMIN GAME HISTORY
# ============================================================

@app.get("/api/admin/history")
def admin_history(
    limit: int = Query(default=100, ge=1, le=500),
    x_telegram_init_data: str = Header(
        default="",
        alias="X-Telegram-Init-Data"
    )
):
    require_admin(x_telegram_init_data)

    return {
        "games": admin_rows("""
            SELECT
                s.id,
                s.telegram_id,
                s.display_name,
                s.score,
                s.mode,
                s.day_key,
                s.duration_seconds,
                s.created_at
            FROM scores s
            ORDER BY s.created_at DESC
            LIMIT ?
        """, (limit,))
    }


# ============================================================
# ADMIN BANNED PLAYERS
# ============================================================

@app.get("/api/admin/banned")
def admin_banned(
    x_telegram_init_data: str = Header(
        default="",
        alias="X-Telegram-Init-Data"
    )
):
    require_admin(x_telegram_init_data)

    return {
        "players": admin_rows("""
            SELECT
                telegram_id,
                username,
                first_name,
                last_name,
                banned
            FROM users
            WHERE banned=1
            ORDER BY telegram_id DESC
        """)
    }


# ============================================================
# ADMIN BAN PLAYER
# ============================================================

@app.post("/api/admin/ban")
def admin_ban(
    action: AdminPlayerAction,
    x_telegram_init_data: str = Header(
        default="",
        alias="X-Telegram-Init-Data"
    )
):
    admin = require_admin(x_telegram_init_data)
    uid = int(action.telegram_id)

    if uid in get_admin_ids():
        raise HTTPException(
            status_code=403,
            detail="An administrator cannot be banned."
        )

    con = db.connect()

    try:
        cursor = con.execute(
            "UPDATE users SET banned=1 WHERE telegram_id=?",
            (uid,)
        )

        con.commit()
        changed = cursor.rowcount

    finally:
        con.close()

    if not changed:
        raise HTTPException(
            status_code=404,
            detail="Player not found."
        )

    record_admin_action(
        admin["id"],
        "BAN_PLAYER",
        uid,
        "Player banned by administrator"
    )

    return {
        "success": True,
        "message": "Player banned."
    }


# ============================================================
# ADMIN UNBAN PLAYER
# ============================================================

@app.post("/api/admin/unban")
def admin_unban(
    action: AdminPlayerAction,
    x_telegram_init_data: str = Header(
        default="",
        alias="X-Telegram-Init-Data"
    )
):
    admin = require_admin(x_telegram_init_data)
    uid = int(action.telegram_id)

    con = db.connect()

    try:
        cursor = con.execute(
            "UPDATE users SET banned=0 WHERE telegram_id=?",
            (uid,)
        )

        con.commit()
        changed = cursor.rowcount

    finally:
        con.close()

    if not changed:
        raise HTTPException(
            status_code=404,
            detail="Player not found."
        )

    record_admin_action(
        admin["id"],
        "UNBAN_PLAYER",
        uid,
        "Player unbanned by administrator"
    )

    return {
        "success": True,
        "message": "Player unbanned."
    }


# ============================================================
# ADMIN ACTIVITY LOG
# ============================================================

@app.get("/api/admin/activity")
def admin_activity(
    limit: int = Query(default=100, ge=1, le=500),
    x_telegram_init_data: str = Header(
        default="",
        alias="X-Telegram-Init-Data"
    )
):
    require_admin(x_telegram_init_data)

    return {
        "activity": admin_rows("""
            SELECT
                id,
                admin_id,
                action,
                target_id,
                details,
                created_at
            FROM admin_activity
            ORDER BY id DESC
            LIMIT ?
        """, (limit,))
    }


# ============================================================
# ADMIN TOURNAMENT INFORMATION
# ============================================================

@app.get("/api/admin/tournament")
def admin_tournament(
    x_telegram_init_data: str = Header(
        default="",
        alias="X-Telegram-Init-Data"
    )
):
    require_admin(x_telegram_init_data)

    return {
        "weekly": {
            "name": "NEON RUSH Weekly Tournament",
            "entry_price_stars": 0,
            "timezone": "Africa/Nairobi",
            "schedule": "Sunday 6:00 PM to Sunday 6:00 PM",
            "session": db.weekly_session(),
            "locked": db.is_weekly_locked(),
            "admission": "Administrator controlled",
            "winners": 5
        },

        "monthly": {
            "name": "NEON RUSH Monthly Premium Tournament",
            "entry_price_stars": MONTHLY_STARS,
            "timezone": "Africa/Nairobi",
            "schedule": "Calendar month",
            "session": db.monthly_session(),
            "prize_percentage": MONTHLY_PRIZE_PERCENTAGE,
            "retained_percentage": MONTHLY_RETAINED_PERCENTAGE,
            "winners": 5
        }
    }


# ============================================================
# ADMIN SETTINGS
# ============================================================

@app.get("/api/admin/settings")
def admin_settings(
    x_telegram_init_data: str = Header(
        default="",
        alias="X-Telegram-Init-Data"
    )
):
    require_admin(x_telegram_init_data)

    return {
        "settings": admin_rows("""
            SELECT key, value
            FROM settings
            ORDER BY key
        """),

        "timezone": "Africa/Nairobi",
        "monthly_price_stars": MONTHLY_STARS,
        "monthly_prize_percentage": MONTHLY_PRIZE_PERCENTAGE,
        "monthly_retained_percentage": MONTHLY_RETAINED_PERCENTAGE,

        "whatsapp": db.get_setting(
            "support_whatsapp",
            SUPPORT_WHATSAPP
        ),

        "email": db.get_setting(
            "support_email",
            SUPPORT_EMAIL
        )
    }


@app.post("/api/admin/settings/contact")
def admin_update_contact(
    action: ContactSettingsAction,
    x_telegram_init_data: str = Header(
        default="",
        alias="X-Telegram-Init-Data"
    )
):
    admin = require_admin(x_telegram_init_data)

    db.set_setting(
        "support_whatsapp",
        action.whatsapp.strip()
    )

    db.set_setting(
        "support_email",
        action.email.strip()
    )

    record_admin_action(
        admin["id"],
        "UPDATE_CONTACT_SETTINGS",
        None,
        "Support contact details updated"
    )

    return {
        "success": True,
        "whatsapp": action.whatsapp.strip(),
        "email": action.email.strip()
    }
