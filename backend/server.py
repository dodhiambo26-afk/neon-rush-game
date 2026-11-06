
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
TOURNAMENT_STARS = 8


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

        if not hmac.compare_digest(calculated_hash, received_hash):
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
    """
    Supports both ADMIN_ID and ADMIN_IDS.
    Multiple IDs can be separated by commas.
    """

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
# DATABASE HELPERS FOR ADMIN DASHBOARD
# ============================================================

def admin_connection():
    return db.connect()


def ensure_admin_audit_table():
    con = admin_connection()

    try:
        con.execute("""
            CREATE TABLE IF NOT EXISTS admin_activity (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                target_id INTEGER,
                details TEXT,
                created_at TEXT NOT NULL
            )
        """)

        con.commit()

    finally:
        con.close()


def record_admin_action(admin_id, action, target_id=None, details=""):
    ensure_admin_audit_table()

    con = admin_connection()

    try:
        con.execute("""
            INSERT INTO admin_activity (
                admin_id,
                action,
                target_id,
                details,
                created_at
            )
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


def admin_rows(sql, params=()):
    con = admin_connection()

    try:
        rows = con.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    finally:
        con.close()


def admin_one(sql, params=()):
    con = admin_connection()

    try:
        row = con.execute(sql, params).fetchone()
        return dict(row) if row else None

    finally:
        con.close()


# ============================================================
# APPLICATION STARTUP
# ============================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    ensure_admin_audit_table()

    bot_task = asyncio.create_task(run_bot())

    try:
        yield
    finally:
        bot_task.cancel()


app = FastAPI(
    title="Neon Rush Game Backend",
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
    mode: str = Field(default="free")
    duration: int = Field(default=0, ge=0, le=3600)


class AdminPlayerAction(BaseModel):
    telegram_id: int = Field(gt=0)


# ============================================================
# PUBLIC BACKEND ROUTES
# ============================================================

@app.get("/")
def home():
    return {
        "status": "online",
        "game": "NEON RUSH",
        "tournament_entry_stars": TOURNAMENT_STARS,
        "tournament_hours": "6:00 AM to 6:00 AM",
        "timezone": "Africa/Nairobi"
    }


@app.get("/health")
def health():
    return {"status": "ok"}


# ============================================================
# TELEGRAM PLAYER AUTHENTICATION
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

    session = db.tournament_session()
    has_entry = db.has_tournament_entry(uid, session["key"])

    return {
        "authenticated": True,
        "user": {
            "id": uid,
            "username": user.get("username"),
            "first_name": user.get("first_name"),
            "last_name": user.get("last_name"),
            "display_name": display_name(user)
        },
        "tournament_access": has_entry,
        "tournament": {
            "session": session["key"],
            "starts_at": session["starts_at"],
            "ends_at": session["ends_at"],
            "timezone": "Africa/Nairobi",
            "entry_price": TOURNAMENT_STARS
        },
        "premium": has_entry
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

    if mode not in ("free", "tournament"):
        raise HTTPException(
            status_code=400,
            detail="Invalid game mode."
        )

    if mode == "tournament":
        session = db.tournament_session()

        if not db.has_tournament_entry(uid, session["key"]):
            raise HTTPException(
                status_code=403,
                detail="Tournament entry required. Pay 8 Telegram Stars to enter."
            )

    db.save_score(
        uid,
        display_name(user),
        submission.score,
        mode,
        submission.duration
    )

    result = {
        "saved": True,
        "score": submission.score,
        "mode": mode
    }

    if mode == "tournament":
        session = db.tournament_session()

        top_rows = db.list_top_scores(
            limit=10,
            session_key=session["key"]
        )

        top_ten = [
            {
                "position": index + 1,
                "telegram_id": int(row["telegram_id"]),
                "name": row["display_name"],
                "score": int(row["score"])
            }
            for index, row in enumerate(top_rows)
        ]

        player = db.get_player_ranking(
            uid,
            session["key"]
        )

        result["leaderboard"] = top_ten
        result["player"] = player
        result["tournament"] = session

    return result


# ============================================================
# PUBLIC LEADERBOARD
# ============================================================

@app.get("/api/leaderboard")
def leaderboard(
    x_telegram_init_data: str = Header(
        default="",
        alias="X-Telegram-Init-Data"
    )
):
    user = authenticate(x_telegram_init_data)
    uid = int(user["id"])

    session = db.tournament_session()

    rows = db.list_top_scores(
        limit=10,
        session_key=session["key"]
    )

    top_ten = [
        {
            "position": index + 1,
            "name": row["display_name"],
            "score": int(row["score"])
        }
        for index, row in enumerate(rows)
    ]

    player = db.get_player_ranking(
        uid,
        session["key"]
    )

    return {
        "leaderboard": top_ten,
        "player": player,
        "tournament": session,
        "has_entry": db.has_tournament_entry(uid, session["key"])
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

    session = db.tournament_session()

    return {
        "has_entry": db.has_tournament_entry(uid, session["key"]),
        "entry_price": TOURNAMENT_STARS,
        "session": session
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
    session = db.tournament_session()

    payment_total = admin_one("""
        SELECT COALESCE(SUM(amount), 0) AS total
        FROM payments
        WHERE currency = 'XTR'
    """)

    paid_entries = admin_one("""
        SELECT COUNT(*) AS total
        FROM tournament_entries
        WHERE session_key = ?
        AND payment_id IS NOT NULL
    """, (session["key"],))

    complimentary_entries = admin_one("""
        SELECT COUNT(*) AS total
        FROM tournament_entries
        WHERE session_key = ?
        AND payment_id IS NULL
    """, (session["key"],))

    return {
        "stats": stats,
        "stars_collected": int(payment_total["total"] or 0),
        "paid_entries_today": int(paid_entries["total"] or 0),
        "complimentary_entries_today": int(
            complimentary_entries["total"] or 0
        ),
        "tournament": session,
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
                    WHERE s.telegram_id = u.telegram_id
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
# ADMIN TOURNAMENT ENTRIES
# ============================================================

@app.get("/api/admin/entries")
def admin_entries(
    x_telegram_init_data: str = Header(
        default="",
        alias="X-Telegram-Init-Data"
    )
):
    require_admin(x_telegram_init_data)

    session = db.tournament_session()

    return {
        "session": session,
        "entries": admin_rows("""
            SELECT
                e.telegram_id,
                e.session_key,
                e.payment_id,
                e.created_at,
                u.username,
                u.first_name,
                u.last_name,
                CASE
                    WHEN e.payment_id IS NULL THEN 'Complimentary'
                    ELSE 'Telegram Stars'
                END AS entry_type
            FROM tournament_entries e
            LEFT JOIN users u
                ON u.telegram_id = e.telegram_id
            WHERE e.session_key = ?
            ORDER BY e.created_at DESC
        """, (session["key"],))
    }


# ============================================================
# ADMIN GRANT COMPLIMENTARY TOURNAMENT ACCESS
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
        "SELECT telegram_id FROM users WHERE telegram_id = ?",
        (uid,)
    )

    if not player:
        raise HTTPException(
            status_code=404,
            detail="Player not found. Ask the player to open the game first."
        )

    session = db.tournament_session()

    if db.has_tournament_entry(uid, session["key"]):
        raise HTTPException(
            status_code=409,
            detail="This player already has tournament access for this session."
        )

    db.grant_tournament_entry(uid, session["key"])

    record_admin_action(
        admin["id"],
        "GRANT_COMPLIMENTARY_ENTRY",
        uid,
        "Complimentary access for session " + session["key"]
    )

    return {
        "success": True,
        "message": "Complimentary tournament access granted.",
        "telegram_id": uid,
        "session": session["key"]
    }


# ============================================================
# ADMIN REVOKE TOURNAMENT ACCESS
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
    session = db.tournament_session()

    con = admin_connection()

    try:
        cursor = con.execute("""
            DELETE FROM tournament_entries
            WHERE telegram_id = ?
            AND session_key = ?
        """, (uid, session["key"]))

        con.commit()
        removed = cursor.rowcount

    finally:
        con.close()

    if not removed:
        raise HTTPException(
            status_code=404,
            detail="No tournament entry was found for this player."
        )

    record_admin_action(
        admin["id"],
        "REVOKE_TOURNAMENT_ENTRY",
        uid,
        "Access revoked for session " + session["key"]
    )

    return {
        "success": True,
        "message": "Tournament access revoked."
    }


# ============================================================
# ADMIN TELEGRAM STARS PAYMENT RECORDS
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
                ON u.telegram_id = p.telegram_id
            ORDER BY p.created_at DESC
            LIMIT ?
        """, (limit,))
    }


# ============================================================
# ADMIN PLAYER PERFORMANCE
# ============================================================

@app.get("/api/admin/performance")
def admin_performance(
    x_telegram_init_data: str = Header(
        default="",
        alias="X-Telegram-Init-Data"
    )
):
    require_admin(x_telegram_init_data)

    session = db.tournament_session()

    return {
        "session": session,
        "top_scores": admin_rows("""
            SELECT
                s.telegram_id,
                MAX(s.display_name) AS display_name,
                MAX(s.score) AS score,
                COUNT(*) AS games_played
            FROM scores s
            WHERE s.mode = 'tournament'
            AND s.day_key = ?
            GROUP BY s.telegram_id
            ORDER BY score DESC
            LIMIT 100
        """, (session["key"],))
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
            WHERE banned = 1
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
            detail="An administrator cannot be banned through this dashboard."
        )

    con = admin_connection()

    try:
        cursor = con.execute(
            "UPDATE users SET banned = 1 WHERE telegram_id = ?",
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

    con = admin_connection()

    try:
        cursor = con.execute(
            "UPDATE users SET banned = 0 WHERE telegram_id = ?",
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
# ADMIN ACTIVITY / AUDIT LOG
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

    ensure_admin_audit_table()

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

    session = db.tournament_session()

    return {
        "name": "NEON RUSH Daily Tournament",
        "entry_price_stars": TOURNAMENT_STARS,
        "timezone": "Africa/Nairobi",
        "schedule": "6:00 AM to 6:00 AM",
        "session": session,
        "payment_method": "Telegram Stars",
        "complimentary_entries": "Administrator controlled"
    }


# ============================================================
# ADMIN SETTINGS VIEW
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
        "tournament_price_stars": TOURNAMENT_STARS,
        "tournament_hours": "6:00 AM to 6:00 AM"
    }
