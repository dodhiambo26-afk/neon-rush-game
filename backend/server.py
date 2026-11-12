
import os
import hmac
import json
import hashlib
import asyncio

from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl
from contextlib import asynccontextmanager
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Header, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

import database as db
from bot import build_application
from telegram import Update
from mpesa import router as mpesa_router


# ============================================================
# CONFIGURATION
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
MAX_AUTH_AGE = 86400

MONTHLY_STARS = 300
MONTHLY_PRIZE_PERCENTAGE = 30
MONTHLY_RETAINED_PERCENTAGE = 70

SUPPORT_WHATSAPP = "+254183375330"
SUPPORT_EMAIL = "neonrusharena@yahoo.com"

KENYA = ZoneInfo("Africa/Nairobi")


# ============================================================
# DATE / SESSION HELPERS
# ============================================================

def utc_now():
    return datetime.now(timezone.utc)


def weekly_session():
    key = db.current_weekly_session()

    start = datetime.strptime(
        key, "%Y-%m-%dT%H:%M"
    ).replace(tzinfo=KENYA)

    end = start + timedelta(days=7)

    return {
        "key": key,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "timezone": "Africa/Nairobi",
    }


def monthly_session():
    key = db.current_month_key()

    year, month = map(int, key.split("-"))

    start = datetime(year, month, 1, tzinfo=KENYA)

    if month == 12:
        end = datetime(year + 1, 1, 1, tzinfo=KENYA)
    else:
        end = datetime(year, month + 1, 1, tzinfo=KENYA)

    return {
        "key": key,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "timezone": "Africa/Nairobi",
    }


def json_safe(value):
    if isinstance(value, datetime):
        return value.isoformat()

    if isinstance(value, dict):
        return {
            str(key): json_safe(item)
            for key, item in value.items()
        }

    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]

    return value


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
        now = int(utc_now().timestamp())

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
            hashlib.sha256,
        ).digest()

        calculated_hash = hmac.new(
            secret_key,
            data_check_string.encode(),
            hashlib.sha256,
        ).hexdigest()

        if not hmac.compare_digest(
            calculated_hash,
            received_hash,
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
            detail=(
                "Telegram authentication failed. "
                "Open the game through Telegram."
            ),
        )

    uid = int(user["id"])

    db.upsert_user(
        uid,
        user.get("username"),
        user.get("first_name"),
        user.get("last_name"),
    )

    if db.is_banned(uid):
        raise HTTPException(
            status_code=403,
            detail="Your account has been blocked.",
        )

    return user


def display_name(user):
    name = " ".join(
        part for part in [
            user.get("first_name"),
            user.get("last_name"),
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
        os.getenv("ADMIN_IDS", ""),
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
            detail=(
                "Telegram verification failed. "
                "Open this page through your Telegram bot."
            ),
        )

    uid = int(user["id"])

    if uid not in get_admin_ids():
        raise HTTPException(
            status_code=403,
            detail=(
                "Access denied. This Telegram account "
                "is not an administrator."
            ),
        )

    return user


# ============================================================
# FIRESTORE ADMIN HELPERS
# ============================================================

def record_admin_action(
    admin_id,
    action,
    target_id=None,
    details="",
):
    return db.record_admin_activity(
        admin_id,
        action,
        target_id,
        {"details": str(details)},
    )


def get_monthly_financial_summary():
    session = monthly_session()
    finances = db.monthly_financials(session["key"])

    collected = int(finances.get("total_stars", 0))

    prize_pool = (
        collected * MONTHLY_PRIZE_PERCENTAGE / 100
    )

    retained = (
        collected * MONTHLY_RETAINED_PERCENTAGE / 100
    )

    return {
        "month": session["key"],
        "stars_collected": collected,
        "mpesa_collected_kes": int(finances.get("total_mpesa_kes", 0)),
        "mpesa_payments_count": int(finances.get("mpesa_payments_count", 0)),
        "prize_percentage": MONTHLY_PRIZE_PERCENTAGE,
        "retained_percentage": MONTHLY_RETAINED_PERCENTAGE,
        "prize_pool_stars": prize_pool,
        "retained_stars": retained,
    }


def user_name_from_record(user):
    name = " ".join(
        part for part in [
            user.get("first_name"),
            user.get("last_name"),
        ] if part
    )

    if name:
        return name

    if user.get("username"):
        return "@" + user["username"]

    return "Player " + str(user.get("telegram_id", ""))


def get_player_best_score(uid):
    best = 0

    for mode in ("weekly", "monthly"):
        periods = db.list_tournament_periods(mode, 500)

        for period in periods:
            key = period.get("period_key")

            scores = db.list_tournament_scores(
                mode,
                key,
                500,
            )

            for score in scores:
                if str(score.get("telegram_id")) == str(uid):
                    best = max(
                        best,
                        int(score.get("score", 0)),
                    )

    return best


# ============================================================
# LEADERBOARD RANKING
# ============================================================

def tournament_player_rankings(mode, session_key):
    return db.get_tournament_ranking(
        mode=mode,
        session_key=session_key,
        limit=10000
    )
    """
    Return one leaderboard record per player.

    Highest score determines rank.
    Equal scores are ranked by the earliest recorded achievement.

    Original individual game records remain in Firestore.
    """

    rows = db.list_tournament_scores(
        mode,
        session_key,
        10000,
    )

    best_by_player = {}

    def recorded_time(row):
        value = row.get("created_at")

        if value is None:
            return float("inf")

        if hasattr(value, "timestamp"):
            try:
                return value.timestamp()
            except (ValueError, OSError):
                return float("inf")

        if isinstance(value, str):
            try:
                parsed = datetime.fromisoformat(
                    value.replace("Z", "+00:00")
                )

                if parsed.tzinfo is None:
                    parsed = parsed.replace(
                        tzinfo=timezone.utc
                    )

                return parsed.timestamp()

            except (ValueError, TypeError, OSError):
                return float("inf")

        return float("inf")

    for row in rows:
        uid = str(row.get("telegram_id", "")).strip()

        if not uid:
            continue

        try:
            score = int(row.get("score", 0))
        except (TypeError, ValueError):
            score = 0

        time_key = recorded_time(row)

        current = best_by_player.get(uid)

        if current is None:
            best_by_player[uid] = {
                "row": row,
                "score": score,
                "time": time_key,
            }

        elif (
            score > current["score"]
            or (
                score == current["score"]
                and time_key < current["time"]
            )
        ):
            best_by_player[uid] = {
                "row": row,
                "score": score,
                "time": time_key,
            }

    ranked = sorted(
        best_by_player.values(),
        key=lambda item: (
            -item["score"],
            item["time"],
            str(item["row"].get("telegram_id", "")),
        ),
    )

    return [
        item["row"]
        for item in ranked
    ]


def build_leaderboard(mode, session_key, limit=10):
    rows = tournament_player_rankings(
        mode,
        session_key,
    )

    output = []

    for index, row in enumerate(rows[:limit], start=1):
        uid = str(row.get("telegram_id", ""))

        output.append({
            "position": index,
            "telegram_id": int(uid) if uid.isdigit() else uid,
            "name": (
                row.get("username")
                or row.get("display_name")
                or uid
                or "Player"
            ),
            "score": int(row.get("score", 0)),
        })

    return output


def player_rank_data(uid, mode, session_key):
    rows = tournament_player_rankings(
        mode,
        session_key,
    )

    for index, row in enumerate(rows, start=1):
        if str(row.get("telegram_id")) == str(uid):
            return {
                "position": index,
                "score": int(row.get("score", 0)),
                "display_name": (
                    row.get("username")
                    or row.get("display_name")
                    or str(uid)
                ),
            }

    return {
        "position": None,
        "score": 0,
        "display_name": None,
    }


# ============================================================
# APPLICATION STARTUP
# ============================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()

    bot_application = build_application()
    app.state.bot_application = bot_application

    await bot_application.initialize()

    if bot_application.post_init:
        await bot_application.post_init(bot_application)

    await bot_application.updater.start_polling(
        allowed_updates=Update.ALL_TYPES
    )

    await bot_application.start()

    print("NEON RUSH ARENA Telegram bot started successfully.")

    try:
        yield

    finally:
        print("Stopping NEON RUSH ARENA Telegram bot...")

        if bot_application.updater.running:
            await bot_application.updater.stop()

        if bot_application.running:
            await bot_application.stop()

        await bot_application.shutdown()


# ============================================================
# FASTAPI APPLICATION
# ============================================================

app = FastAPI(
    title="NEON RUSH Game Backend",
    version="3.0",
    lifespan=lifespan,
)

app.include_router(mpesa_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://dodhiambo26-afk.github.io",
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=[
        "Content-Type",
        "X-Telegram-Init-Data",
    ],
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
    funding_source: str = Field(
        default="Administrator",
        max_length=100,
    )
    reference: str | None = Field(
        default=None,
        max_length=200,
    )
    notes: str | None = Field(
        default=None,
        max_length=1000,
    )


class PrizeAwardAction(BaseModel):
    period_type: str
    period_key: str
    position: int = Field(ge=1, le=5)
    telegram_id: int = Field(gt=0)
    amount: float = Field(ge=0)
    currency: str = Field(default="KES", max_length=10)
    notes: str | None = Field(
        default=None,
        max_length=1000,
    )


class PrizePaidAction(BaseModel):
    payment_reference: str | None = Field(
        default=None,
        max_length=200,
    )
    notes: str | None = Field(
        default=None,
        max_length=1000,
    )


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
    weekly = weekly_session()
    monthly = monthly_session()

    return {
        "status": "online",
        "game": "NEON RUSH ARENA",
        "version": "3.0",
        "weekly_tournament": {
            "schedule": "Sunday 6:00 PM to Sunday 6:00 PM",
            "timezone": "Africa/Nairobi",
            "entry_price": 0,
            "locked": db.is_weekly_locked(),
            "session": weekly,
        },
        "monthly_tournament": {
            "price_stars": MONTHLY_STARS,
            "expiry": "End of calendar month",
            "session": monthly,
        },
    }


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/api/contact")
def public_contact():
    return {
        "whatsapp": db.get_setting(
            "support_whatsapp",
            SUPPORT_WHATSAPP,
        ),
        "email": db.get_setting(
            "support_email",
            SUPPORT_EMAIL,
        ),
    }


# ============================================================
# TELEGRAM PLAYER AUTHENTICATION
# ============================================================

@app.post("/api/auth/telegram")
def telegram_auth(
    x_telegram_init_data: str = Header(
        default="",
        alias="X-Telegram-Init-Data",
    ),
):
    user = authenticate(x_telegram_init_data)
    uid = int(user["id"])

    weekly = weekly_session()
    monthly = monthly_session()

    weekly_access = db.has_weekly_entry(
        uid,
        weekly["key"],
    )

    monthly_access = db.has_monthly_access(
        uid,
        monthly["key"],
    )

    weekly_locked = db.is_weekly_locked()

    contact = {
        "whatsapp": db.get_setting(
            "support_whatsapp",
            SUPPORT_WHATSAPP,
        ),
        "email": db.get_setting(
            "support_email",
            SUPPORT_EMAIL,
        ),
    }

    return {
        "authenticated": True,

        "user": {
            "id": uid,
            "username": user.get("username"),
            "first_name": user.get("first_name"),
            "last_name": user.get("last_name"),
            "display_name": display_name(user),
        },

        "weekly_access": weekly_access,
        "weekly_locked": weekly_locked,
        "weekly_session": weekly,

        "monthly_access": monthly_access,
        "monthly_session": monthly,
        "monthly_expiry": monthly["end"],

        "weekly": {
            "session": weekly,
            "has_access": weekly_access,
            "locked": weekly_locked,
            "entry_price": 0,
        },

        "monthly": {
            "session": monthly,
            "has_access": monthly_access,
            "entry_price": MONTHLY_STARS,
            "expires_at": monthly["end"],
        },

        "premium": monthly_access,
        "contact": contact,
    }


# ============================================================
# GAME SCORE SUBMISSION
# ============================================================

@app.post("/api/scores")
def submit_score(
    submission: ScoreSubmission,
    x_telegram_init_data: str = Header(
        default="",
        alias="X-Telegram-Init-Data",
    ),
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
            "player": None,
        }

    if mode not in ("weekly", "monthly"):
        raise HTTPException(
            status_code=400,
            detail="Invalid game mode. Use free, weekly or monthly.",
        )

    if mode == "weekly":
        session = weekly_session()

        if not db.has_weekly_entry(uid, session["key"]):
            raise HTTPException(
                status_code=403,
                detail=(
                    "You have not been admitted to "
                    "this week's tournament."
                ),
            )

    else:
        session = monthly_session()

        if not db.has_monthly_access(uid, session["key"]):
            raise HTTPException(
                status_code=403,
                detail=(
                    "Your monthly membership is not active. "
                    "Purchase monthly access through the Telegram bot."
                ),
            )

    saved = db.save_score(
        uid=uid,
        score=submission.score,
        mode=mode,
        session_key=session["key"],
        username=display_name(user),
    )

    if not saved:
        raise HTTPException(
            status_code=403,
            detail="Score could not be saved. Check your tournament access.",
        )

    leaderboard_data = build_leaderboard(
        mode,
        session["key"],
        10,
    )

    player = player_rank_data(
        uid,
        mode,
        session["key"],
    )

    return {
        "saved": True,
        "score": submission.score,
        "mode": mode,
        "leaderboard": leaderboard_data,
        "player": player,
        "tournament": session,
    }


# ============================================================
# PUBLIC LEADERBOARD
# ============================================================

@app.get("/api/leaderboard")
def leaderboard(
    mode: str = Query(default="weekly"),
    x_telegram_init_data: str = Header(
        default="",
        alias="X-Telegram-Init-Data",
    ),
):
    user = authenticate(x_telegram_init_data)
    uid = int(user["id"])

    mode = mode.lower().strip()

    if mode == "weekly":
        session = weekly_session()
        has_access = db.has_weekly_entry(
            uid,
            session["key"],
        )

    elif mode == "monthly":
        session = monthly_session()
        has_access = db.has_monthly_access(
            uid,
            session["key"],
        )

    else:
        raise HTTPException(
            status_code=400,
            detail="Leaderboard mode must be weekly or monthly.",
        )

    data = build_leaderboard(
        mode,
        session["key"],
        10,
    )

    player = (
        player_rank_data(uid, mode, session["key"])
        if has_access
        else {
            "position": None,
            "score": 0,
            "display_name": None,
        }
    )

    return {
        "mode": mode,
        "leaderboard": data,
        "player": player,
        "has_access": has_access,
        "tournament": session,
    }


# ============================================================
# PUBLIC TOURNAMENT STATUS
# ============================================================

@app.get("/api/tournament/status")
def tournament_status(
    x_telegram_init_data: str = Header(
        default="",
        alias="X-Telegram-Init-Data",
    ),
):
    user = authenticate(x_telegram_init_data)
    uid = int(user["id"])

    weekly = weekly_session()
    monthly = monthly_session()

    weekly_entries = db.list_weekly_entries(weekly["key"])
    monthly_members = db.list_monthly_members(monthly["key"])

    def count_active_players(records):
        player_ids = set()

        for record in records:
            try:
                player_id = int(record.get("telegram_id"))
            except (TypeError, ValueError):
                continue

            if player_id > 0 and not db.is_banned(player_id):
                player_ids.add(player_id)

        return len(player_ids)

    weekly_count = count_active_players(weekly_entries)
    monthly_count = count_active_players(monthly_members)

    return {
        "weekly": {
            "session": weekly,
            "has_access": db.has_weekly_entry(
                uid,
                weekly["key"],
            ),
            "locked": db.is_weekly_locked(),
            "entry_price": 0,
            "active_players": weekly_count,
        },

        "monthly": {
            "session": monthly,
            "has_access": db.has_monthly_access(
                uid,
                monthly["key"],
            ),
            "entry_price": MONTHLY_STARS,
            "active_players": monthly_count,
        },
    }


# ============================================================
# ADMIN ACCESS CHECK
# ============================================================

@app.get("/api/admin/check")
def admin_check(
    x_telegram_init_data: str = Header(
        default="",
        alias="X-Telegram-Init-Data",
    ),
):
    user = require_admin(x_telegram_init_data)

    return {
        "authorized": True,
        "admin": {
            "id": int(user["id"]),
            "name": display_name(user),
        },
    }


# ============================================================
# ADMIN OVERVIEW
# ============================================================

@app.get("/api/admin/overview")
def admin_overview(
    x_telegram_init_data: str = Header(
        default="",
        alias="X-Telegram-Init-Data",
    ),
):
    admin = require_admin(x_telegram_init_data)

    stats = db.get_stats()
    weekly = weekly_session()
    monthly = monthly_session()

    monthly_finances = get_monthly_financial_summary()

    funding_rows = db.list_weekly_funding(weekly["key"])
    prize_rows = db.list_prize_awards("weekly", weekly["key"])

    funding_total = sum(
        float(row.get("amount", 0))
        for row in funding_rows
        if row.get("currency") == "KES"
    )

    prizes_total = sum(
        float(row.get("amount", 0))
        for row in prize_rows
        if row.get("currency") == "KES"
    )

    return {
        "stats": stats,
        "players": stats.get("players", 0),
        "stars_collected": stats.get("stars", 0),

        "weekly": {
            "session": weekly,
            "locked": db.is_weekly_locked(),
            "entries": len(db.list_weekly_entries(weekly["key"])),
            "funding_kes": funding_total,
            "prizes_kes": prizes_total,
        },

        "monthly": {
            "session": monthly,
            "members": len(db.list_monthly_members(monthly["key"])),
            "financials": monthly_finances,
        },

        "admin_id": int(admin["id"]),
    }


# ============================================================
# ADMIN PLAYERS
# ============================================================

@app.get("/api/admin/players")
def admin_players(
    q: str = Query(default="", max_length=100),
    limit: int = Query(default=100, ge=1, le=500),
    x_telegram_init_data: str = Header(
        default="",
        alias="X-Telegram-Init-Data",
    ),
):
    require_admin(x_telegram_init_data)

    players = db.search_players(q)

    players.sort(
        key=lambda player: str(
            player.get("created_at", "")
        ),
        reverse=True,
    )

    result = []

    for player in players[:limit]:
        uid = player.get("telegram_id")

        result.append({
            "telegram_id": int(uid),
            "username": player.get("username"),
            "first_name": player.get("first_name"),
            "last_name": player.get("last_name"),
            "banned": bool(player.get("banned", False)),
            "created_at": player.get("created_at"),
            "best_score": get_player_best_score(uid),
        })

    return {"players": json_safe(result)}


# ============================================================
# ADMIN WEEKLY ENTRIES
# ============================================================

@app.get("/api/admin/entries")
def admin_entries(
    x_telegram_init_data: str = Header(
        default="",
        alias="X-Telegram-Init-Data",
    ),
):
    require_admin(x_telegram_init_data)

    session = weekly_session()
    entries = db.list_weekly_entries(session["key"])

    result = []

    for entry in entries:
        uid = entry.get("telegram_id")
        player = db.get_user(uid) or {}

        result.append({
            **entry,
            "telegram_id": uid,
            "username": player.get("username") or "",
            "first_name": player.get("first_name") or "",
            "last_name": player.get("last_name") or "",
            "display_name": (
                " ".join(filter(None, [
                    player.get("first_name"),
                    player.get("last_name")
                ]))
                or player.get("username")
                or str(uid)
            ),
            "admitted_by": entry.get("granted_by") or "",
            "created_at": entry.get("granted_at") or "",
        })

    return {
        "session": session,
        "locked": db.is_weekly_locked(),
        "entries": json_safe(result),
    }


# ============================================================
# ADMIN WEEKLY DIAGNOSTIC
# ============================================================

@app.get("/api/admin/weekly-diagnostic")
def admin_weekly_diagnostic(
    x_telegram_init_data: str = Header(
        default="",
        alias="X-Telegram-Init-Data",
    ),
):
    require_admin(x_telegram_init_data)

    diagnostic = db.weekly_entries_diagnostic()

    return {
        "current_session": weekly_session(),
        "weekly_entries_total": diagnostic.get(
            "weekly_entries_total", 0
        ),
        "weekly_entries_current": diagnostic.get(
            "weekly_entries_current", 0
        ),
        "legacy_entries_total": 0,
        "legacy_entries_current": 0,
        "weekly_session_records": diagnostic.get(
            "weekly_session_records", []
        ),
        "legacy_session_records": [],
        "note": "Legacy paid daily tournament has been removed.",
    }


# ============================================================
# ADMIN GRANT WEEKLY ENTRY
# ============================================================

@app.post("/api/admin/grant")
def admin_grant(
    action: AdminPlayerAction,
    x_telegram_init_data: str = Header(
        default="",
        alias="X-Telegram-Init-Data",
    ),
):
    admin = require_admin(x_telegram_init_data)
    uid = int(action.telegram_id)

    player = db.get_user(uid)

    if not player:
        raise HTTPException(
            status_code=404,
            detail="Player not found. Ask the player to open the game first.",
        )

    session = weekly_session()

    if db.has_weekly_entry(uid, session["key"]):
        raise HTTPException(
            status_code=409,
            detail="This player already has weekly tournament access.",
        )

    db.grant_weekly_entry(
        uid,
        session["key"],
        int(admin["id"]),
    )

    return {
        "success": True,
        "message": "Weekly tournament access granted.",
        "telegram_id": uid,
        "session": session["key"],
    }


# ============================================================
# ADMIN REVOKE WEEKLY ENTRY
# ============================================================

@app.post("/api/admin/revoke")
def admin_revoke(
    action: AdminPlayerAction,
    x_telegram_init_data: str = Header(
        default="",
        alias="X-Telegram-Init-Data",
    ),
):
    admin = require_admin(x_telegram_init_data)
    uid = int(action.telegram_id)

    session = weekly_session()

    removed = db.revoke_weekly_entry(
        uid,
        session["key"],
    )

    if not removed:
        raise HTTPException(
            status_code=404,
            detail="No weekly tournament entry was found.",
        )

    record_admin_action(
        admin["id"],
        "REVOKE_WEEKLY_ENTRY",
        uid,
        "Weekly tournament " + session["key"],
    )

    return {
        "success": True,
        "message": "Weekly tournament access revoked.",
    }


# ============================================================
# ADMIN WEEKLY LOCK / UNLOCK
# ============================================================

@app.post("/api/admin/weekly/lock")
def admin_weekly_lock(
    action: WeeklyLockAction,
    x_telegram_init_data: str = Header(
        default="",
        alias="X-Telegram-Init-Data",
    ),
):
    admin = require_admin(x_telegram_init_data)

    db.set_weekly_locked(
        action.locked,
        admin["id"],
    )

    record_admin_action(
        admin["id"],
        "SET_WEEKLY_LOCK",
        None,
        "Weekly locked: " + str(action.locked),
    )

    return {
        "success": True,
        "locked": action.locked,
    }


# ============================================================
# ADMIN MONTHLY MEMBERS
# ============================================================

@app.get("/api/admin/monthly/members")
def admin_monthly_members(
    x_telegram_init_data: str = Header(
        default="",
        alias="X-Telegram-Init-Data",
    ),
):
    require_admin(x_telegram_init_data)

    session = monthly_session()
    members = db.list_monthly_members(session["key"])

    result = []

    for member in members:
        uid = member.get("telegram_id")
        player = db.get_user(uid) or {}

        result.append({
            **member,
            "telegram_id": uid,
            "username": player.get("username") or "",
            "first_name": player.get("first_name") or "",
            "last_name": player.get("last_name") or "",
            "display_name": (
                " ".join(filter(None, [
                    player.get("first_name"),
                    player.get("last_name")
                ]))
                or player.get("username")
                or str(uid)
            ),
            "starts_at": member.get("granted_at") or "",
            "payment_id": member.get("payment_id") or "",
            "granted_by": member.get("granted_by") or "",
        })

    return {
        "session": session,
        "members": json_safe(result),
    }


# ============================================================
# ADMIN GRANT MONTHLY ACCESS
# ============================================================

@app.post("/api/admin/monthly/grant")
def admin_monthly_grant(
    action: MonthlyGrantAction,
    x_telegram_init_data: str = Header(
        default="",
        alias="X-Telegram-Init-Data",
    ),
):
    admin = require_admin(x_telegram_init_data)

    uid = int(action.telegram_id)

    if not db.get_user(uid):
        raise HTTPException(
            status_code=404,
            detail="Player not found.",
        )

    session = monthly_session()

    db.grant_monthly_access(
        uid,
        session["key"],
        int(admin["id"]),
    )

    return {
        "success": True,
        "message": "Monthly access granted.",
        "expires_at": session["end"],
    }


# ============================================================
# ADMIN REVOKE MONTHLY ACCESS
# ============================================================

@app.post("/api/admin/monthly/revoke")
def admin_monthly_revoke(
    action: AdminPlayerAction,
    x_telegram_init_data: str = Header(
        default="",
        alias="X-Telegram-Init-Data",
    ),
):
    admin = require_admin(x_telegram_init_data)

    uid = int(action.telegram_id)
    session = monthly_session()

    removed = db.revoke_monthly_access(
        uid,
        session["key"],
    )

    if not removed:
        raise HTTPException(
            status_code=404,
            detail="No monthly membership was found.",
        )

    record_admin_action(
        admin["id"],
        "REVOKE_MONTHLY_ACCESS",
        uid,
        "Monthly membership " + session["key"],
    )

    return {
        "success": True,
        "message": "Monthly access revoked.",
    }


# ============================================================
# ADMIN WEEKLY FUNDING
# ============================================================

@app.get("/api/admin/weekly/funding")
def admin_weekly_funding(
    x_telegram_init_data: str = Header(
        default="",
        alias="X-Telegram-Init-Data",
    ),
):
    require_admin(x_telegram_init_data)

    session = weekly_session()

    rows = db.list_weekly_funding(session["key"])

    total_by_currency = {}

    for row in rows:
        currency = row.get("currency", "KES")

        total_by_currency[currency] = (
            total_by_currency.get(currency, 0)
            + float(row.get("amount", 0))
        )

    return {
        "session": session,
        "funding": json_safe(rows),
        "totals": total_by_currency,
    }


@app.post("/api/admin/weekly/funding")
def admin_add_weekly_funding(
    action: WeeklyFundingAction,
    x_telegram_init_data: str = Header(
        default="",
        alias="X-Telegram-Init-Data",
    ),
):
    admin = require_admin(x_telegram_init_data)

    session = weekly_session()

    funding_id = db.add_weekly_funding(
        session_key=session["key"],
        admin_id=int(admin["id"]),
        amount=action.amount,
        currency=action.currency.upper(),
        funding_source=action.funding_source,
        reference=action.reference,
        notes=action.notes,
    )

    record_admin_action(
        admin["id"],
        "ADD_WEEKLY_PRIZE_FUNDING",
        None,
        f"{action.amount} {action.currency}",
    )

    return {
        "success": True,
        "funding_id": funding_id,
    }


# ============================================================
# ADMIN MONTHLY FINANCIALS
# ============================================================

@app.get("/api/admin/monthly/financials")
def admin_monthly_financials(
    x_telegram_init_data: str = Header(
        default="",
        alias="X-Telegram-Init-Data",
    ),
):
    require_admin(x_telegram_init_data)

    return get_monthly_financial_summary()


# ============================================================
# ADMIN PRIZES
# ============================================================

@app.get("/api/admin/prizes")
def admin_prizes(
    period_type: str = Query(default=""),
    period_key: str = Query(default=""),
    x_telegram_init_data: str = Header(
        default="",
        alias="X-Telegram-Init-Data",
    ),
):
    require_admin(x_telegram_init_data)

    if period_type and period_type not in ("weekly", "monthly"):
        raise HTTPException(
            status_code=400,
            detail="Invalid prize period.",
        )

    return {
        "awards": json_safe(
            db.list_prize_awards(
                period_type or None,
                period_key or None,
            )
        ),
    }


@app.post("/api/admin/prizes")
def admin_create_prize(
    action: PrizeAwardAction,
    x_telegram_init_data: str = Header(
        default="",
        alias="X-Telegram-Init-Data",
    ),
):
    admin = require_admin(x_telegram_init_data)

    if action.period_type not in ("weekly", "monthly"):
        raise HTTPException(
            status_code=400,
            detail="Prize period must be weekly or monthly.",
        )

    if not db.get_user(action.telegram_id):
        raise HTTPException(
            status_code=404,
            detail="Prize winner was not found.",
        )

    award_id = db.save_prize_award(
        period_type=action.period_type,
        period_key=action.period_key,
        position=action.position,
        telegram_id=action.telegram_id,
        amount=action.amount,
        currency=action.currency.upper(),
        notes=action.notes,
    )

    record_admin_action(
        admin["id"],
        "CREATE_PRIZE_AWARD",
        action.telegram_id,
        (
            f"{action.period_type} {action.period_key}, "
            f"position {action.position}"
        ),
    )

    return {
        "success": True,
        "award_id": award_id,
    }


@app.post("/api/admin/prizes/{award_id}/paid")
def admin_mark_prize_paid(
    award_id: str,
    action: PrizePaidAction,
    x_telegram_init_data: str = Header(
        default="",
        alias="X-Telegram-Init-Data",
    ),
):
    admin = require_admin(x_telegram_init_data)

    changed = db.mark_prize_paid(
        award_id,
        action.payment_reference,
        action.notes,
    )

    if not changed:
        raise HTTPException(
            status_code=404,
            detail="Prize record not found.",
        )

    record_admin_action(
        admin["id"],
        "MARK_PRIZE_PAID",
        None,
        f"Prize award {award_id}",
    )

    return {
        "success": True,
        "message": "Prize payout recorded.",
    }


# ============================================================
# ADMIN PERFORMANCE
# ============================================================

@app.get("/api/admin/performance")
def admin_performance(
    mode: str = Query(default="weekly"),
    x_telegram_init_data: str = Header(
        default="",
        alias="X-Telegram-Init-Data",
    ),
):
    require_admin(x_telegram_init_data)

    mode = mode.lower().strip()

    if mode == "weekly":
        session = weekly_session()

    elif mode == "monthly":
        session = monthly_session()

    else:
        raise HTTPException(
            status_code=400,
            detail="Mode must be weekly or monthly.",
        )

    scores = db.get_tournament_ranking(
    mode=mode,
    session_key=session["key"],
    limit=100
)
    return {
        "mode": mode,
        "session": session,
        "top_scores": json_safe(scores),
        "top_five": json_safe(scores[:5]),
    }


# ============================================================
# ADMIN PAYMENTS
# ============================================================

@app.get("/api/admin/payments")
def admin_payments(
    limit: int = Query(default=100, ge=1, le=500),
    x_telegram_init_data: str = Header(
        default="",
        alias="X-Telegram-Init-Data",
    ),
):
    require_admin(x_telegram_init_data)

    payments = db.list_payments(limit)

    result = []

    for payment in payments:
        uid = payment.get("telegram_id")
        user = db.get_user(uid) or {}

        result.append({
            **payment,
            "username": user.get("username"),
            "first_name": user.get("first_name"),
            "last_name": user.get("last_name"),
        })

    return {"payments": json_safe(result)}


# ============================================================
# ADMIN GAME HISTORY
# ============================================================

@app.get("/api/admin/history")
def admin_history(
    limit: int = Query(default=100, ge=1, le=500),
    x_telegram_init_data: str = Header(
        default="",
        alias="X-Telegram-Init-Data",
    ),
):
    require_admin(x_telegram_init_data)

    history = []

    for mode in ("weekly", "monthly"):
        periods = db.list_tournament_periods(mode, 500)

        for period in periods:
            key = period.get("period_key")

            scores = db.list_tournament_scores(
                mode,
                key,
                500,
            )

            for score in scores:
                history.append({
                    "telegram_id": score.get("telegram_id"),
                    "display_name": score.get("username"),
                    "score": score.get("score"),
                    "mode": mode,
                    "day_key": key,
                    "created_at": score.get("created_at"),
                })

    history.sort(
        key=lambda item: str(item.get("created_at", "")),
        reverse=True,
    )

    return {"games": json_safe(history[:limit])}


# ============================================================
# ADMIN TOURNAMENT HISTORY
# ============================================================

def validate_history_period_type(period_type):
    period_type = period_type.lower().strip()

    if period_type not in ("weekly", "monthly"):
        raise HTTPException(
            status_code=400,
            detail="Period type must be weekly or monthly.",
        )

    return period_type


@app.get("/api/admin/history/summary")
def admin_tournament_history_summary(
    x_telegram_init_data: str = Header(
        default="",
        alias="X-Telegram-Init-Data",
    ),
):
    require_admin(x_telegram_init_data)

    return json_safe(
        db.get_tournament_history_summary()
    )


@app.get("/api/admin/history/periods")
def admin_tournament_history_periods(
    period_type: str = Query(default="weekly"),
    limit: int = Query(default=100, ge=1, le=500),
    x_telegram_init_data: str = Header(
        default="",
        alias="X-Telegram-Init-Data",
    ),
):
    require_admin(x_telegram_init_data)

    period_type = validate_history_period_type(period_type)

    periods = db.list_tournament_periods(
        period_type,
        limit,
    )

    return {
        "period_type": period_type,
        "periods": json_safe(periods),
    }


@app.get("/api/admin/history/{period_type}/{period_key}")
def admin_tournament_history_detail(
    period_type: str,
    period_key: str,
    x_telegram_init_data: str = Header(
        default="",
        alias="X-Telegram-Init-Data",
    ),
):
    require_admin(x_telegram_init_data)

    period_type = validate_history_period_type(period_type)

    if not period_key or len(period_key) > 30:
        raise HTTPException(
            status_code=400,
            detail="Invalid tournament period key.",
        )

    history = db.get_tournament_history(
        period_type,
        period_key,
    )

    if not history:
        raise HTTPException(
            status_code=404,
            detail="Tournament history was not found.",
        )

    return json_safe(history)


# ============================================================
# ADMIN BANNED PLAYERS
# ============================================================

@app.get("/api/admin/banned")
def admin_banned(
    x_telegram_init_data: str = Header(
        default="",
        alias="X-Telegram-Init-Data",
    ),
):
    require_admin(x_telegram_init_data)

    return {
        "players": json_safe(
            db.list_banned_players()
        ),
    }


# ============================================================
# ADMIN BAN PLAYER
# ============================================================

@app.post("/api/admin/ban")
def admin_ban(
    action: AdminPlayerAction,
    x_telegram_init_data: str = Header(
        default="",
        alias="X-Telegram-Init-Data",
    ),
):
    admin = require_admin(x_telegram_init_data)
    uid = int(action.telegram_id)

    if uid in get_admin_ids():
        raise HTTPException(
            status_code=403,
            detail="An administrator cannot be banned.",
        )

    if not db.get_user(uid):
        raise HTTPException(
            status_code=404,
            detail="Player not found.",
        )

    db.set_banned(uid, True, admin["id"])

    record_admin_action(
        admin["id"],
        "BAN_PLAYER",
        uid,
        "Player banned by administrator",
    )

    return {
        "success": True,
        "message": "Player banned.",
    }


# ============================================================
# ADMIN UNBAN PLAYER
# ============================================================

@app.post("/api/admin/unban")
def admin_unban(
    action: AdminPlayerAction,
    x_telegram_init_data: str = Header(
        default="",
        alias="X-Telegram-Init-Data",
    ),
):
    admin = require_admin(x_telegram_init_data)
    uid = int(action.telegram_id)

    if not db.get_user(uid):
        raise HTTPException(
            status_code=404,
            detail="Player not found.",
        )

    db.set_banned(uid, False, admin["id"])

    record_admin_action(
        admin["id"],
        "UNBAN_PLAYER",
        uid,
        "Player unbanned by administrator",
    )

    return {
        "success": True,
        "message": "Player unbanned.",
    }


# ============================================================
# ADMIN ACTIVITY LOG
# ============================================================

@app.get("/api/admin/activity")
def admin_activity(
    limit: int = Query(default=100, ge=1, le=500),
    x_telegram_init_data: str = Header(
        default="",
        alias="X-Telegram-Init-Data",
    ),
):
    require_admin(x_telegram_init_data)

    return {
        "activity": json_safe(
            db.list_admin_activity(limit)
        ),
    }


# ============================================================
# ADMIN TOURNAMENT INFORMATION
# ============================================================

@app.get("/api/admin/tournament")
def admin_tournament(
    x_telegram_init_data: str = Header(
        default="",
        alias="X-Telegram-Init-Data",
    ),
):
    require_admin(x_telegram_init_data)

    return {
        "weekly": {
            "name": "NEON RUSH Weekly Tournament",
            "entry_price_KES": 25,
            "timezone": "Africa/Nairobi",
            "schedule": "Sunday 6:00 PM to Sunday 6:00 PM",
            "session": weekly_session(),
            "locked": db.is_weekly_locked(),
            "admission": "Administrator controlled",
            "winners": 5,
        },

        "monthly": {
            "name": "NEON RUSH Monthly Premium Tournament",
            "entry_price_stars": MONTHLY_STARS,
            "timezone": "Africa/Nairobi",
            "schedule": "Calendar month",
            "session": monthly_session(),
            "prize_percentage": MONTHLY_PRIZE_PERCENTAGE,
            "retained_percentage": MONTHLY_RETAINED_PERCENTAGE,
            "winners": 5,
        },
    }


# ============================================================
# ADMIN SETTINGS
# ============================================================

@app.get("/api/admin/settings")
def admin_settings(
    x_telegram_init_data: str = Header(
        default="",
        alias="X-Telegram-Init-Data",
    ),
):
    require_admin(x_telegram_init_data)

    settings = db.list_settings()

    return {
        "settings": settings,
        "timezone": "Africa/Nairobi",
        "monthly_price_stars": MONTHLY_STARS,
        "monthly_prize_percentage": MONTHLY_PRIZE_PERCENTAGE,
        "monthly_retained_percentage": MONTHLY_RETAINED_PERCENTAGE,

        "whatsapp": db.get_setting(
            "support_whatsapp",
            SUPPORT_WHATSAPP,
        ),

        "email": db.get_setting(
            "support_email",
            SUPPORT_EMAIL,
        ),
    }


@app.post("/api/admin/settings/contact")
def admin_update_contact(
    action: ContactSettingsAction,
    x_telegram_init_data: str = Header(
        default="",
        alias="X-Telegram-Init-Data",
    ),
):
    admin = require_admin(x_telegram_init_data)

    db.set_setting(
        "support_whatsapp",
        action.whatsapp.strip(),
        admin["id"],
    )

    db.set_setting(
        "support_email",
        action.email.strip(),
        admin["id"],
    )

    record_admin_action(
        admin["id"],
        "UPDATE_CONTACT_SETTINGS",
        None,
        "Support contact details updated",
    )

    return {
        "success": True,
        "whatsapp": action.whatsapp.strip(),
        "email": action.email.strip(),
    }
