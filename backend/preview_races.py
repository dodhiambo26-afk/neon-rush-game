"""Isolated, in-memory race test endpoints for the Render preview service only."""
import os
import secrets
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from race_engine import simulate_race

router = APIRouter(prefix="/api/preview/races", tags=["preview-races"])
PREVIEW_MODE = os.getenv("PREVIEW_MODE", "").strip().lower() in {"1", "true", "yes", "on"}
PREVIEW_TEST_KEY = os.getenv("PREVIEW_TEST_KEY", "")
SESSIONS = {}


def check_key(value):
    if not PREVIEW_MODE:
        raise HTTPException(status_code=404, detail="Not found.")
    if not PREVIEW_TEST_KEY or not secrets.compare_digest(value or "", PREVIEW_TEST_KEY):
        raise HTTPException(status_code=401, detail="A valid preview test key is required.")


class StartRequest(BaseModel):
    mode: str = Field(default="weekly", pattern="^(weekly|monthly)$")


class InputEvent(BaseModel):
    tick: int = Field(ge=1, le=432000)
    direction: int = Field(ge=-1, le=1)


class SubmitRequest(BaseModel):
    race_id: str = Field(min_length=20, max_length=128)
    ticks: int = Field(ge=1, le=432000)
    inputs: list[InputEvent] = Field(default_factory=list, max_length=10000)


@router.post("/start")
def start_preview_race(
    request: StartRequest,
    x_preview_test_key: str = Header(default="", alias="X-Preview-Test-Key"),
):
    check_key(x_preview_test_key)
    race_id = secrets.token_urlsafe(32)
    seed = secrets.randbits(32) or 1
    SESSIONS[race_id] = {
        "seed": seed,
        "mode": request.mode,
        "created": time.time(),
        "used": False,
    }
    return {
        "race_id": race_id,
        "seed": seed,
        "mode": request.mode,
        "simulation": {"width": 390, "height": 844, "tick_rate": 60, "version": 1},
        "expires_in_seconds": 7200,
        "storage": "temporary_memory_only",
        "production_data": "not_connected",
    }


@router.post("/submit")
def submit_preview_race(
    request: SubmitRequest,
    x_preview_test_key: str = Header(default="", alias="X-Preview-Test-Key"),
):
    check_key(x_preview_test_key)
    session = SESSIONS.get(request.race_id)
    if not session or session["used"]:
        raise HTTPException(status_code=409, detail="Preview race is missing or already used.")
    if time.time() - session["created"] > 7200:
        SESSIONS.pop(request.race_id, None)
        raise HTTPException(status_code=410, detail="Preview race has expired.")
    if time.time() - session["created"] < 5:
        raise HTTPException(status_code=422, detail="Play for at least five seconds before submitting.")
    try:
        replay = simulate_race(
            session["seed"],
            [item.dict() for item in request.inputs],
            request.ticks,
        )
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if not replay["ended"] or replay["ticks"] != request.ticks:
        raise HTTPException(status_code=422, detail="Race replay is incomplete; submit a completed race.")
    session["used"] = True
    return {
        "verified": True,
        "mode": session["mode"],
        "server_score": replay["score"],
        "coins": replay["coins"],
        "lives": replay["lives"],
        "ticks": replay["ticks"],
        "ended": replay["ended"],
        "saved_to_leaderboard": False,
        "storage": "temporary_memory_only",
        "production_data": "not_connected",
        "verified_at": datetime.now(timezone.utc).isoformat(),
    }
