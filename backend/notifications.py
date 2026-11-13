"""NEON RUSH ARENA player notification service.

The scheduler runs inside the existing FastAPI lifespan, so no second Render
service or external cron subscription is required.
"""
import asyncio
import html
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import database as db

logger = logging.getLogger(__name__)
KENYA = ZoneInfo("Africa/Nairobi")
PRIZE_PERCENTAGES = {1: 10, 2: 5, 3: 3, 4: 2, 5: 1}


def _now():
    return datetime.now(timezone.utc)


def _local_now():
    return datetime.now(KENYA)


def _user_preferences(user):
    stored = user.get("notification_preferences") or {}
    return {
        "tournament_alerts": bool(stored.get("tournament_alerts", True)),
        "winner_alerts": bool(stored.get("winner_alerts", True)),
        "daily_reminders": bool(stored.get("daily_reminders", False)),
        "announcements": bool(stored.get("announcements", False)),
    }


def get_preferences(uid):
    user = db.get_user(uid) or {}
    return _user_preferences(user)


def set_preferences(uid, preferences):
    allowed = {"tournament_alerts", "winner_alerts", "daily_reminders", "announcements"}
    clean = {key: bool(value) for key, value in preferences.items() if key in allowed}
    user = db.get_user(uid)
    if not user:
        return None
    current = _user_preferences(user)
    current.update(clean)
    db.db().collection("users").document(str(uid)).set({
        "notification_preferences": current,
        "notification_preferences_updated_at": db.timestamp(),
    }, merge=True)
    return current


def _eligible_users(preference=None, inactive_days=None):
    rows = db.db().collection("users").stream()
    result = []
    for snap in rows:
        user = snap.to_dict() or {}
        if user.get("banned"):
            continue
        try:
            uid = int(user.get("telegram_id") or snap.id)
        except (TypeError, ValueError):
            continue
        if uid <= 0:
            continue
        if preference and not _user_preferences(user).get(preference, False):
            continue
        if inactive_days is not None:
            last_active = user.get("updated_at") or user.get("created_at")
            if not isinstance(last_active, datetime):
                continue
            if last_active.tzinfo is None:
                last_active = last_active.replace(tzinfo=timezone.utc)
            if last_active > _now() - timedelta(days=inactive_days):
                continue
        result.append(uid)
    return result


def _claim_event(event_id, event_type):
    """Claim an event once; stale in-progress claims can be retried after 10m."""
    ref = db.db().collection("notification_events").document(event_id)
    transaction = db.db().transaction()

    @db.firestore.transactional
    def claim(txn):
        snapshot = ref.get(transaction=txn)
        current = snapshot.to_dict() if snapshot.exists else {}
        status = current.get("status")
        claimed_at = current.get("claimed_at")
        if status == "sent":
            return False
        if status == "sending" and claimed_at:
            age = (_now() - claimed_at.replace(tzinfo=timezone.utc)).total_seconds()
            if age < 600:
                return False
        txn.set(ref, {
            "event_id": event_id,
            "event_type": event_type,
            "status": "sending",
            "claimed_at": _now(),
            "updated_at": _now(),
            "attempts": int(current.get("attempts", 0)) + 1,
        }, merge=True)
        return True

    return claim(transaction)


def _finish_event(event_id, status, sent=0, failed=0, detail=None):
    db.db().collection("notification_events").document(event_id).set({
        "status": status,
        "sent": int(sent),
        "failed": int(failed),
        "detail": str(detail or "")[:500],
        "updated_at": _now(),
        "completed_at": _now() if status == "sent" else None,
    }, merge=True)


async def _deliver(application, event_id, text, preference=None, user_ids=None, inactive_days=None):
    if not _claim_event(event_id, "scheduled" if preference else "broadcast"):
        return {"skipped": True, "sent": 0, "failed": 0}

    recipients = user_ids if user_ids is not None else _eligible_users(preference, inactive_days)
    sent = failed = 0
    for uid in recipients:
        try:
            await application.bot.send_message(
                chat_id=uid,
                text=text,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
            sent += 1
        except Exception as exc:
            failed += 1
            logger.warning("Notification delivery failed for %s: %s", uid, exc)
        await asyncio.sleep(0.05)

    _finish_event(event_id, "sent", sent, failed)
    db.record_admin_activity(
        None, "NOTIFICATION_DELIVERY", None,
        {"event_id": event_id, "sent": sent, "failed": failed},
    )
    return {"skipped": False, "sent": sent, "failed": failed}


async def send_admin_broadcast(application, message, category="announcement", admin_id=None):
    category = str(category or "announcement")
    if category not in {"announcement", "service"}:
        raise ValueError("Category must be announcement or service.")
    preference = "announcements" if category == "announcement" else None
    recipients = _eligible_users(preference)
    event_id = "admin_" + str(int(_now().timestamp() * 1000))
    result = await _deliver(
        application,
        event_id,
        "📢 <b>NEON RUSH ARENA</b>\n\n" + html.escape(message),
        preference=preference,
        user_ids=recipients,
    )
    db.record_admin_activity(
        admin_id, "SEND_NOTIFICATION_BROADCAST", None,
        {"category": category, "event_id": event_id,
         "recipient_count": len(recipients), **result},
    )
    return {"event_id": event_id, "recipients": len(recipients), **result}


def _format_winners(mode, period_key):
    ranked_rows = db.get_tournament_ranking(mode, period_key, 100)
    rows = [
        row for row in ranked_rows
        if not db.is_banned(row.get("telegram_id"))
    ][:5]
    if not rows:
        return None
    title = "WEEKLY" if mode == "weekly" else "MONTHLY"
    lines = [
        f"🏁 <b>NEON RUSH ARENA — {title} RESULTS</b>",
        "",
        "Congratulations to the Top 5 racers!",
    ]
    medals = ["🥇", "🥈", "🥉", "🏅", "🎖️"]
    for index, row in enumerate(rows[:5], start=1):
        name = (row.get("username") or row.get("display_name")
                or row.get("name") or f"Player {row.get('telegram_id')}")
        safe_name = str(name).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        percent = PRIZE_PERCENTAGES[index]
        lines.append(f"{medals[index - 1]} <b>{index}.</b> {safe_name} — {percent}% of collected tournament funds")
    lines.extend([
        "",
        "Final standings and cash awards are subject to administrator verification. Cash payouts are handled separately.",
        "Keep racing: your next chance to reach the Top 5 is coming!",
    ])
    return "\n".join(lines)


async def _run_scheduled_checks(application):
    now = _local_now()
    current_key = db.current_weekly_session()
    current_start = datetime.strptime(current_key, "%Y-%m-%dT%H:%M").replace(tzinfo=KENYA)
    next_start = current_start + timedelta(days=7)

    for hours_before, label in ((24, "24-hour"), (1, "1-hour")):
        target = next_start - timedelta(hours=hours_before)
        if target <= now < target + timedelta(minutes=20):
            event_id = f"weekly_{next_start.strftime('%Y%m%d%H%M')}_{hours_before}h"
            await _deliver(
                application,
                event_id,
                f"⏰ <b>NEON RUSH ARENA REMINDER</b>\n\nThe next weekly tournament starts in {label}!\nGet ready to race and compete for the Top 5 cash-reward positions.",
                preference="tournament_alerts",
            )

    # Daily free-game reminder at 6:00 PM Kenya time, with a 20-minute catch-up window.
    if now.hour == 18 and now.minute < 20:
        event_id = "daily_free_game_" + now.strftime("%Y%m%d")
        await _deliver(
            application,
            event_id,
            "🎮 <b>Your NEON RUSH daily game is ready!</b>\n\nCome back, play for free, and beat your best score.\n\n👉 https://t.me/NeonRushGamingBot",
            preference="daily_reminders",
        )

    # Monthly tournament opening notice on the first day of the month.
    if now.day == 1 and now.hour == 9 and now.minute < 20:
        event_id = "monthly_open_" + now.strftime("%Y%m")
        await _deliver(
            application,
            event_id,
            "🌟 <b>A new NEON RUSH monthly tournament has started!</b>\n\nYour next chance to climb the leaderboard and compete for cash rewards is here.",
            preference="tournament_alerts",
        )

    # Announce the previous weekly results shortly after the Sunday 6 PM rollover.
    if now.weekday() == 6 and now.hour == 18 and now.minute < 20:
        previous_start = current_start - timedelta(days=7)
        previous_key = previous_start.strftime("%Y-%m-%dT%H:%M")
        message = _format_winners("weekly", previous_key)
        if message:
            await _deliver(
                application,
                "weekly_winners_" + previous_start.strftime("%Y%m%d%H%M"),
                message,
                preference="winner_alerts",
            )

    # Announce monthly results shortly after the new month begins.
    if now.day == 1 and now.hour == 0 and now.minute < 20:
        previous_month = (now.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
        message = _format_winners("monthly", previous_month)
        if message:
            await _deliver(
                application,
                "monthly_winners_" + previous_month.replace("-", ""),
                message,
                preference="winner_alerts",
            )


async def notification_scheduler(application):
    """Run scheduled messages while the production backend is alive."""
    logger.info("NEON RUSH notification scheduler started (Africa/Nairobi).")
    while True:
        try:
            await _run_scheduled_checks(application)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Notification scheduler cycle failed.")
        await asyncio.sleep(60)
