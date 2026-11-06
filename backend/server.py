
import os
import hmac
import json
import hashlib
import asyncio

from datetime import datetime, timezone
from urllib.parse import parse_qsl

from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

import database as db
from bot import run_bot


BOT_TOKEN = os.getenv("BOT_TOKEN", "")
MAX_AUTH_AGE = 86400
TOURNAMENT_STARS = 8


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


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()

    bot_task = asyncio.create_task(run_bot())

    yield

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
    allow_headers=["Content-Type", "X-Telegram-Init-Data"]
)


class ScoreSubmission(BaseModel):
    score: int = Field(ge=0, le=100000)
    mode: str = Field(default="free")
    duration: int = Field(default=0, ge=0, le=3600)


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

        # Temporary compatibility with the previous game version.
        "premium": has_entry
    }


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
