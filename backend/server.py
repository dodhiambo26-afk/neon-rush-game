
import os
import asyncio
import hashlib
import hmac
import json
import time

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from urllib.parse import parse_qsl

from fastapi import FastAPI, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

import database as db
from bot import run_bot


BOT_TOKEN = os.getenv("BOT_TOKEN", "")
MAX_AUTH_AGE = 86400


# =====================================
# TELEGRAM AUTHENTICATION
# =====================================

def verify_telegram_data(init_data: str):

    if not BOT_TOKEN or not init_data:
        return None

    try:

        pairs = dict(parse_qsl(init_data, keep_blank_values=True))

        received_hash = pairs.pop("hash", None)
        auth_date = int(pairs.get("auth_date", "0"))

        if not received_hash or time.time() - auth_date > MAX_AUTH_AGE:
            return None

        if auth_date > time.time() + 60:
            return None

        data_check = "\n".join(
            f"{key}={value}"
            for key, value in sorted(pairs.items())
        )

        secret = hmac.new(
            b"WebAppData",
            BOT_TOKEN.encode(),
            hashlib.sha256
        ).digest()

        calculated = hmac.new(
            secret,
            data_check.encode(),
            hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(calculated, received_hash):
            return None

        user = json.loads(pairs["user"])

        return user if isinstance(user, dict) and user.get("id") else None

    except (
        ValueError,
        KeyError,
        TypeError,
        json.JSONDecodeError
    ):
        return None


# =====================================
# DATABASE AND BOT STARTUP
# =====================================

@asynccontextmanager
async def lifespan(app: FastAPI):

    db.init_db()

    task = asyncio.create_task(run_bot())
    app.state.bot_task = task

    try:
        yield

    finally:

        task.cancel()

        try:
            await task
        except asyncio.CancelledError:
            pass


# =====================================
# FASTAPI APPLICATION
# =====================================

app = FastAPI(
    title="NEON RUSH API",
    lifespan=lifespan
)


# =====================================
# CORS - ALLOW GITHUB PAGES GAME
# =====================================

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://dodhiambo26-afk.github.io"
    ],
    allow_credentials=False,
    allow_methods=[
        "GET",
        "POST",
        "OPTIONS"
    ],
    allow_headers=[
        "Content-Type",
        "X-Telegram-Init-Data"
    ],
)


# =====================================
# SCORE DATA MODEL
# =====================================

class ScoreSubmission(BaseModel):

    score: int = Field(
        ge=0,
        le=100000
    )

    mode: str

    duration: int = Field(
        ge=1,
        le=3600
    )


# =====================================
# VERIFY TELEGRAM PLAYER
# =====================================

def authenticate(init_data):

    user = verify_telegram_data(init_data or "")

    if not user:
        raise HTTPException(
            status_code=401,
            detail="Invalid Telegram authentication"
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
            detail="Account restricted"
        )

    return user


# =====================================
# HOME / HEALTH CHECK
# =====================================

@app.get("/")
def home():

    return {
        "status": "online",
        "game": "NEON RUSH",
        "version": "2.0"
    }


# =====================================
# TELEGRAM LOGIN
# =====================================

@app.post("/api/auth/telegram")
def telegram_auth(
    x_telegram_init_data: str = Header(default="")
):

    user = authenticate(x_telegram_init_data)

    uid = int(user["id"])

    record = db.get_user(uid)

    premium = False

    if record and record["premium_until"]:

        expiry = datetime.fromisoformat(
            record["premium_until"]
        )

        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)

        premium = expiry > datetime.now(timezone.utc)

    return {
        "authenticated": True,

        "user": {
            "id": uid,
            "name": user.get("first_name") or "Player",
            "username": user.get("username")
        },

        "premium": premium
    }


# =====================================
# SAVE PLAYER SCORE
# =====================================

@app.post("/api/scores")
def submit_score(
    submission: ScoreSubmission,
    x_telegram_init_data: str = Header(default="")
):

    user = authenticate(x_telegram_init_data)

    if submission.mode not in ("free", "tournament"):

        raise HTTPException(
            status_code=400,
            detail="Invalid game mode"
        )

    # Premium verification for tournaments

    if submission.mode == "tournament":

        record = db.get_user(int(user["id"]))

        if not record or not record["premium_until"]:

            raise HTTPException(
                status_code=403,
                detail="Premium required"
            )

        expiry = datetime.fromisoformat(
            record["premium_until"]
        )

        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)

        if expiry <= datetime.now(timezone.utc):

            raise HTTPException(
                status_code=403,
                detail="Premium membership expired"
            )

    name = (
        user.get("first_name")
        or user.get("username")
        or "Player"
    )

    db.save_score(
        int(user["id"]),
        name,
        submission.score,
        submission.mode,
        submission.duration
    )

    return {
        "saved": True,
        "score": submission.score,
        "mode": submission.mode
    }


# =====================================
# GLOBAL LEADERBOARD
# =====================================

@app.get("/api/leaderboard")
def leaderboard():

    rows = db.list_top_scores(10)

    return {
        "leaderboard": [
            {
                "position": i,
                "name": row["display_name"],
                "score": row["score"]
            }

            for i, row in enumerate(rows, 1)
        ]
    }
