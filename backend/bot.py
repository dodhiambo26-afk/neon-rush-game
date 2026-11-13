import os
import asyncio
import logging
import html

from datetime import datetime, timezone

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    WebAppInfo,
    LabeledPrice,
)

from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    PreCheckoutQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

import database as db
from mpesa import initiate_stk_push


# ============================================================
# CONFIGURATION
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "")

WEBAPP_URL = os.getenv(
    "WEBAPP_URL",
    "https://dodhiambo26-afk.github.io/neon-rush-game/"
)
COMMUNITY_URL = "https://t.me/neonrusharenacomunity"
LOGO_URL = (
    "https://dodhiambo26-afk.github.io/neon-rush-game/nra_logo.png"
)

ADMIN_DASHBOARD_URL = os.getenv(
    "ADMIN_DASHBOARD_URL",
    "https://dodhiambo26-afk.github.io/neon-rush-game/admin.html"
)

MONTHLY_PRICE = 300
MONTHLY_CURRENCY = "XTR"

SUPPORT_WHATSAPP = "+254183375330"
SUPPORT_EMAIL = os.getenv(
    "SUPPORT_EMAIL",
    "neonrusharena@gmail.com"
)

ADMIN_IDS = set()

for value in os.getenv("ADMIN_ID", "").split(","):
    value = value.strip()
    if value.isdigit():
        ADMIN_IDS.add(int(value))

for value in os.getenv("ADMIN_IDS", "").split(","):
    value = value.strip()
    if value.isdigit():
        ADMIN_IDS.add(int(value))


logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


# ============================================================
# HELPERS
# ============================================================

def is_admin(uid):
    return int(uid) in ADMIN_IDS


def current_weekly_session():
    return db.current_weekly_session()


def current_month_session():
    return db.current_month_key()


def game_url(mode="free"):
    separator = "&" if "?" in WEBAPP_URL else "?"
    return f"{WEBAPP_URL}{separator}mode={mode}"


def support_text():
    return (
        "NEON RUSH ARENA SUPPORT\n\n"
        f"WhatsApp: {SUPPORT_WHATSAPP}\n"
        f"Email: {SUPPORT_EMAIL}\n\n"
        "For payment or account issues, include your Telegram ID."
    )


def reject_banned(uid):
    return db.is_banned(uid)


def ensure_user(update):
    user = update.effective_user

    if not user:
        return

    db.upsert_user(
        uid=user.id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
    )


def player_keyboard(uid):
    keyboard = [
        [
            InlineKeyboardButton(
                "🏁 PLAY FREE DAILY GAME",
                web_app=WebAppInfo(url=f"{WEBAPP_URL}?mode=free")
            )
        ],
        [
            InlineKeyboardButton(
                "🏆 WEEKLY TOURNAMENT",
                callback_data="weekly_info"
            ),
            InlineKeyboardButton(
                "👑 MONTHLY MEMBERSHIP",
                callback_data="monthly_join"
            )
        ],
        [
            InlineKeyboardButton(
                "📊 WEEKLY LEADERBOARD",
                callback_data="weekly_leaderboard"
            ),
            InlineKeyboardButton(
                "👑 MONTHLY LEADERBOARD",
                callback_data="monthly_leaderboard"
            )
        ],
        [
            InlineKeyboardButton(
                "👤 MY PROFILE",
                callback_data="my_profile"
            )
        ],
        [
            InlineKeyboardButton(
                "🌍 JOIN GLOBAL COMMUNITY",
                url=COMMUNITY_URL
            )
        ],
        [
            InlineKeyboardButton(
                "📩 SUPPORT",
                callback_data="support"
            )
        ]
    ]

    if uid in ADMIN_IDS:
        keyboard.append([
            InlineKeyboardButton(
                "🛠 ADMIN DASHBOARD",
                web_app=WebAppInfo(url=ADMIN_DASHBOARD_URL)
            )
        ])

    return InlineKeyboardMarkup(keyboard)

    if is_admin(uid):
        buttons.append([
            InlineKeyboardButton(
                "⚙️ ADMIN DASHBOARD",
                web_app=WebAppInfo(url=ADMIN_DASHBOARD_URL)
            )
        ])

    return InlineKeyboardMarkup(buttons)


def main_text(user):
    name = html.escape(user.first_name or "Player")

    return (
        f"🚀 Welcome to NEON RUSH ARENA, {name}!\n\n"
        "Choose your game mode:\n\n"
        "🎮 FREE DAILY GAME\n"
        "Play free every day.\n\n"
        "🏆 WEEKLY TOURNAMENT\n"
        "Entry: KES 25 by M-Pesa or 50 Telegram Stars.\n"
        "Weekly session: Sunday 6 PM to Sunday 6 PM "
        "(Kenya time).\n"
        "\n<b>━━━━━━━━━━━━━━━━━━━━</b>\n"
        "🏆 <b>TOP 5 PLAYERS WIN REWARDS!</b> 🏆\n"
        "<b>In EVERY weekly tournament.</b>\n"
        "<b>━━━━━━━━━━━━━━━━━━━━</b>\n\n"
        "🌟 MONTHLY TOURNAMENT\n"
        "Membership: KSh 200 by M-Pesa or 300 Telegram Stars.\n"
        "\n<b>━━━━━━━━━━━━━━━━━━━━</b>\n"
        "🏆 <b>TOP 5 PLAYERS WIN REWARDS!</b> 🏆\n"
        "<b>In EVERY monthly tournament.</b>\n"
        "<b>━━━━━━━━━━━━━━━━━━━━</b>\n"
        "<i>Final standings are reviewed before awards are confirmed. Cash payouts are handled separately and are not automatic.</i>\n\n"
        "Good luck, Racer!"
    )


# ============================================================
# START
# ============================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    ensure_user(update)

    if reject_banned(user.id):
        await update.message.reply_text(
            "🚫 Your account has been suspended."
        )
        return

    await update.message.reply_photo(
        photo=LOGO_URL,
        caption=main_text(user),
        reply_markup=player_keyboard(user.id),
        parse_mode="HTML",
    )

# ============================================================
# WEEKLY TOURNAMENT
# ============================================================

async def weekly_information(update: Update, context):
    query = update.callback_query
    await query.answer()

    uid = query.from_user.id

    if reject_banned(uid):
        await query.message.reply_text(
            "🚫 Your account has been suspended."
        )
        return

    session_key = current_weekly_session()

    if db.has_weekly_entry(uid, session_key):
        await query.message.reply_text(
            "✅ You already have access to this weekly tournament.\n\n"
            "Tap the button below to play.",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🏆 PLAY WEEKLY TOURNAMENT",
                        web_app=WebAppInfo(url=game_url("weekly"))
                    )
                ]
            ]),
        )
        return

    await query.message.reply_text(
        f"🏆 WEEKLY TOURNAMENT — {session_key}\n\n"
        "Entry fee: KSh 25 by M-Pesa OR 50 Telegram Stars.\n\n"
        "Choose your payment method below. Your entry activates only "
        "after the payment is confirmed.\n\n"
        "Session: Sunday 6 PM to Sunday 6 PM Kenya time.\n\n"
        "\n━━━━━━━━━━━━━━━━━━━━\n"
        "🏆 <b>TOP 5 PLAYERS WIN REWARDS!</b> 🏆\n"
        "<b>Every weekly tournament.</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<i>Final standings are reviewed before awards are confirmed. Cash payouts are not automatic.</i>",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📱 PAY KSh 25 WITH M-PESA", callback_data="weekly_mpesa")],
            [InlineKeyboardButton("⭐ PAY 50 TELEGRAM STARS", callback_data="weekly_stars")],
            [InlineKeyboardButton("📞 SUPPORT", callback_data="support")],
        ]),
    )


# ============================================================
# MONTHLY TOURNAMENT
# ============================================================

async def monthly_join_options(update, context):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id

    if reject_banned(uid):
        await query.message.reply_text("🚫 Your account has been suspended.")
        return

    month_key = current_month_session()
    if db.has_monthly_access(uid, month_key):
        await query.message.reply_text(
            "✅ Your monthly tournament membership is already active.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    "🏆 PLAY MONTHLY TOURNAMENT",
                    web_app=WebAppInfo(url=game_url("monthly"))
                )
            ]]),
        )
        return

    await query.message.reply_text(
        f"👑 MONTHLY MEMBERSHIP — {month_key}\n\n"
        "Choose your payment method:\n"
        "• KSh 200 via M-Pesa STK Push\n"
        "• 300 Telegram Stars\n\n"
        "\n━━━━━━━━━━━━━━━━━━━━\n"
        "🏆 <b>TOP 5 PLAYERS WIN REWARDS!</b> 🏆\n"
        "<b>Every monthly tournament.</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<i>Final standings are reviewed before awards are confirmed. Cash payouts are not automatic.</i>",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📱 PAY KSh 200 WITH M-PESA", callback_data="monthly_mpesa")],
            [InlineKeyboardButton("⭐ PAY 300 TELEGRAM STARS", callback_data="monthly_stars")],
        ]),
    )


async def send_monthly_invoice(update, context):
    query = update.callback_query
    await query.answer()

    uid = query.from_user.id

    if reject_banned(uid):
        await query.message.reply_text(
            "🚫 Your account has been suspended."
        )
        return

    month_key = current_month_session()

    if db.has_monthly_access(uid, month_key):
        await query.message.reply_text(
            "✅ You already have monthly tournament access.",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🏆 PLAY MONTHLY TOURNAMENT",
                        web_app=WebAppInfo(url=game_url("monthly"))
                    )
                ]
            ]),
        )
        return

    await context.bot.send_invoice(
        chat_id=uid,
        title="NEON RUSH ARENA Monthly Membership",
        description=(
            f"Monthly tournament membership for {month_key}. "
            "Compete on the monthly leaderboard."
        ),
        payload=f"monthly_membership_{month_key}",
        currency=MONTHLY_CURRENCY,
        prices=[
            LabeledPrice(
                label="Monthly Tournament Membership",
                amount=MONTHLY_PRICE,
            )
        ],
        provider_token="",
    )


async def send_weekly_invoice(update, context):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    if reject_banned(uid):
        await query.message.reply_text("🚫 Your account has been suspended.")
        return
    session_key = current_weekly_session()
    if db.has_weekly_entry(uid, session_key):
        await query.message.reply_text("✅ You already have access to this week's tournament.")
        return
    await context.bot.send_invoice(
        chat_id=uid,
        title="NEON RUSH ARENA Weekly Tournament Entry",
        description=f"Weekly tournament entry for session {session_key}.",
        payload=f"weekly_entry_{session_key}",
        currency="XTR",
        prices=[LabeledPrice(label="Weekly Tournament Entry", amount=50)],
        provider_token="",
    )


# ============================================================
# LEADERBOARDS
# ============================================================

async def request_mpesa_phone(update, context, payment_type="monthly_membership"):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id

    if reject_banned(uid):
        await query.message.reply_text("🚫 Your account has been suspended.")
        return

    if payment_type == "weekly_entry":
        session_key = current_weekly_session()
        if db.has_weekly_entry(uid, session_key):
            await query.message.reply_text("✅ You already have access to this week's tournament.")
            return
        context.user_data["awaiting_mpesa_phone"] = "weekly_entry"
        heading = "📱 M-PESA WEEKLY TOURNAMENT ENTRY"
        price = "KSh 25"
    else:
        if db.has_monthly_access(uid, current_month_session()):
            await query.message.reply_text("✅ Your monthly membership is already active.")
            return
        context.user_data["awaiting_mpesa_phone"] = "monthly_membership"
        heading = "📱 M-PESA MONTHLY MEMBERSHIP"
        price = "KSh 200"

    await query.message.reply_text(
        heading + "\n\n"
        f"The fixed payment price is {price}.\n"
        "Send your Safaricom M-Pesa number in this chat (for example 0712345678).\n\n"
        "You will receive an STK prompt on that phone. Enter your M-Pesa PIN only on your phone's Safaricom prompt — never send your PIN here."
    )


async def mpesa_phone_message(update, context):
    payment_type = context.user_data.get("awaiting_mpesa_phone")
    if not payment_type:
        return

    context.user_data["awaiting_mpesa_phone"] = False
    uid = update.effective_user.id

    if reject_banned(uid):
        await update.message.reply_text("🚫 Your account has been suspended.")
        return

    try:
        result = await initiate_stk_push(
            uid, update.message.text.strip(), payment_type=payment_type
        )
        if result.get("already_active"):
            await update.message.reply_text("✅ You already have access to this tournament period.")
        else:
            amount_text = "KSh 25" if payment_type == "weekly_entry" else "KSh 200"
            access_text = "weekly tournament entry" if payment_type == "weekly_entry" else "monthly membership"
            await update.message.reply_text(
                "📲 M-PESA request sent. " + result["customer_message"] + "\n\n"
                f"Your {access_text} will activate only after Safaricom confirms the {amount_text} payment."
            )
    except ValueError as exc:
        await update.message.reply_text(str(exc) + " Please send the number again.")
        context.user_data["awaiting_mpesa_phone"] = payment_type
    except Exception:
        logger.exception("M-Pesa STK Push initiation failed.")
        await update.message.reply_text(
            "❌ We could not start the M-Pesa request. No tournament access has been activated. "
            "Please try again later or contact support."
        )


async def leaderboard_message(update, context, mode):
    query = update.callback_query
    await query.answer()

    uid = query.from_user.id

    if reject_banned(uid):
        await query.message.reply_text(
            "🚫 Your account has been suspended."
        )
        return

    if mode == "weekly":
        session_key = current_weekly_session()
        title = "🏆 WEEKLY LEADERBOARD"
    else:
        session_key = current_month_session()
        title = "🌟 MONTHLY LEADERBOARD"

    scores = db.get_tournament_ranking(
    mode=mode,
    session_key=session_key,
    limit=10,
)

    if not scores:
        await query.message.reply_text(
            f"{title}\n\n"
            "No scores have been recorded yet."
        )
        return

    lines = [
        title,
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        "🏆 <b>TOP 5 PLAYERS WIN REWARDS!</b> 🏆",
        "<b>Final standings are reviewed before awards are confirmed.</b>",
        "<i>Cash payouts are handled separately and are not automatic.</i>",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
    ]

    for position, item in enumerate(scores, start=1):
        username = html.escape(str(item.get("username") or item.get("telegram_id")))
        score = item.get("score", 0)

        lines.append(
            f"{position}. {username} — {score} points"
        )

    await query.message.reply_text("\n".join(lines), parse_mode="HTML")


# ============================================================
# PROFILE
# ============================================================

async def profile_message(update, context):
    query = update.callback_query
    await query.answer()

    uid = query.from_user.id

    if reject_banned(uid):
        await query.message.reply_text(
            "🚫 Your account has been suspended."
        )
        return

    user = db.get_user(uid) or {}
    profile = db.get_player_profile(uid, history_limit=5)

    weekly_access = db.has_weekly_entry(
        uid,
        current_weekly_session()
    )

    monthly_access = db.has_monthly_access(
        uid,
        current_month_session()
    )

    achievements = profile.get("achievements") or []
    recent_games = profile.get("recent_games") or []

    lines = [
        "👤 NEON RUSH PLAYER PROFILE",
        "",
        f"Player: {user.get('first_name') or 'Racer'}",
        f"Username: @{user.get('username') or 'Not set'}",
        f"Telegram ID: {uid}",
        "",
        "📊 CAREER STATISTICS",
        f"Races played: {profile.get('games_played', 0)}",
        f"Total points: {profile.get('total_points', 0):,}",
        f"Personal best: {profile.get('personal_best', 0):,}",
        f"Weekly best: {profile.get('weekly_best', 0):,}",
        f"Monthly best: {profile.get('monthly_best', 0):,}",
        "",
        "🏆 CURRENT TOURNAMENT RANKS",
        f"Weekly rank: #{profile['weekly_rank']}" if profile.get("weekly_rank") else "Weekly rank: Not ranked yet",
        f"Monthly rank: #{profile['monthly_rank']}" if profile.get("monthly_rank") else "Monthly rank: Not ranked yet",
        "",
        "🔐 MEMBERSHIP STATUS",
        f"Weekly access: {'✅ Active' if weekly_access else '❌ Not active'}",
        f"Monthly access: {'✅ Active' if monthly_access else '❌ Not active'}",
        "",
        "🎖 ACHIEVEMENTS",
        ", ".join(achievements) if achievements else "No achievements yet — complete your first race!",
        "",
        "🕹 RECENT RACES",
    ]

    if recent_games:
        for game in recent_games:
            lines.append(
                f"• {str(game.get('mode', 'race')).title()}: "
                f"{game.get('score', 0):,} points"
            )
    else:
        lines.append("No tournament races recorded yet.")

    await query.message.reply_text("\n".join(lines))


# ============================================================
# CALLBACK BUTTONS
# ============================================================

async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    if query.data == "weekly_info":
        await weekly_information(update, context)

    elif query.data == "weekly_stars":
        await send_weekly_invoice(update, context)

    elif query.data == "weekly_mpesa":
        await request_mpesa_phone(update, context, "weekly_entry")

    elif query.data == "monthly_join":
        await monthly_join_options(update, context)

    elif query.data == "monthly_stars":
        await send_monthly_invoice(update, context)

    elif query.data == "monthly_mpesa":
        await request_mpesa_phone(update, context)

    elif query.data == "weekly_leaderboard":
        await leaderboard_message(update, context, "weekly")

    elif query.data == "monthly_leaderboard":
        await leaderboard_message(update, context, "monthly")

    elif query.data == "my_profile":
        await profile_message(update, context)

    elif query.data == "support":
        await query.answer()
        await query.message.reply_text(support_text())


# ============================================================
# TELEGRAM STARS PRE-CHECKOUT
# ============================================================

async def precheckout(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.pre_checkout_query
    uid = query.from_user.id

    if reject_banned(uid):
        await query.answer(ok=False, error_message="Your account is suspended.")
        return

    if query.currency != "XTR":
        await query.answer(ok=False, error_message="Invalid payment currency.")
        return

    if query.invoice_payload.startswith("weekly_entry_"):
        expected = f"weekly_entry_{current_weekly_session()}"
        if query.invoice_payload != expected or query.total_amount != 50:
            await query.answer(ok=False, error_message="This weekly entry invoice is invalid or expired.")
            return
        if db.has_weekly_entry(uid, current_weekly_session()):
            await query.answer(ok=False, error_message="You already have this week's entry.")
            return
        await query.answer(ok=True)
        return

    expected_monthly = f"monthly_membership_{current_month_session()}"
    if query.invoice_payload != expected_monthly or query.total_amount != MONTHLY_PRICE:
        await query.answer(ok=False, error_message="This membership invoice is invalid or expired.")
        return
    if db.has_monthly_access(uid, current_month_session()):
        await query.answer(ok=False, error_message="Your monthly membership is already active.")
        return

    await query.answer(ok=True)

# ============================================================
# SUCCESSFUL PAYMENT
# ============================================================

async def successful_payment(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    user = update.effective_user
    payment = update.message.successful_payment

    uid = user.id

    if reject_banned(uid):
        await update.message.reply_text(
            "🚫 Your account is suspended."
        )
        return

    if payment.currency != "XTR":
        await update.message.reply_text("❌ Invalid payment currency.")
        return

    if payment.invoice_payload.startswith("weekly_entry_"):
        session_key = current_weekly_session()
        expected = f"weekly_entry_{session_key}"
        if payment.invoice_payload != expected or payment.total_amount != 50:
            await update.message.reply_text("❌ Invalid or expired weekly entry payment.")
            return
        try:
            recorded = db.record_weekly_stars_payment(
                uid=uid,
                telegram_charge_id=payment.telegram_payment_charge_id,
                provider_charge_id=payment.provider_payment_charge_id,
                amount=payment.total_amount,
                currency=payment.currency,
                session_key=session_key,
            )
            if not recorded:
                await update.message.reply_text("This payment has already been processed.")
                return
            await update.message.reply_text(
                "🎉 PAYMENT SUCCESSFUL!\n\n"
                "Your 50-Star weekly tournament entry is now active.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🏆 PLAY WEEKLY TOURNAMENT",
                                         web_app=WebAppInfo(url=game_url("weekly")))
                ]]),
            )
        except Exception:
            logger.exception("Weekly Stars payment processing failed")
            await update.message.reply_text("Payment received, but entry activation needs support assistance.")
        return

    expected_monthly = f"monthly_membership_{current_month_session()}"
    if payment.invoice_payload != expected_monthly or payment.total_amount != MONTHLY_PRICE:
        await update.message.reply_text("❌ Invalid or expired membership payment.")
        return

    try:
        recorded = db.record_monthly_payment(
            uid=uid,
            telegram_charge_id=payment.telegram_payment_charge_id,
            provider_charge_id=payment.provider_payment_charge_id,
            amount=payment.total_amount,
            currency=payment.currency,
        )

        if not recorded:
            await update.message.reply_text(
                "This payment has already been processed."
            )
            return

        await update.message.reply_text(
            "🎉 PAYMENT SUCCESSFUL!\n\n"
            "Your monthly tournament membership is now active.\n\n"
            "Good luck in NEON RUSH ARENA!",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🏆 PLAY MONTHLY TOURNAMENT",
                        web_app=WebAppInfo(url=game_url("monthly"))
                    )
                ]
            ]),
        )

    except Exception:
        logger.exception("Monthly payment processing failed")

        await update.message.reply_text(
            "Your payment was received, but membership activation "
            "could not be completed automatically. Please contact support."
        )


# ============================================================
# USER COMMANDS
# ============================================================

async def myid(update: Update, context):
    await update.message.reply_text(
        f"Your Telegram ID: {update.effective_user.id}"
    )


async def command_leaderboard(update: Update, context):
    await update.message.reply_text(
        "Choose a leaderboard:",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🏆 Weekly",
                    callback_data="weekly_leaderboard"
                )
            ],
            [
                InlineKeyboardButton(
                    "🌟 Monthly",
                    callback_data="monthly_leaderboard"
                )
            ],
        ]),
    )


async def paysupport(update: Update, context):
    await update.message.reply_text(support_text())


# ============================================================
# ADMIN COMMAND
# ============================================================

async def admin(update: Update, context):
    uid = update.effective_user.id

    if not is_admin(uid):
        await update.message.reply_text(
            "🚫 Unauthorized."
        )
        return

    stats = db.get_stats()

    await update.message.reply_text(
        "⚙️ NEON RUSH ARENA ADMIN\n\n"
        f"Players: {stats.get('users', 0)}\n"
        f"Banned players: {stats.get('banned', 0)}\n"
        f"Payments: {stats.get('payments', 0)}\n"
        f"Telegram Stars received: {stats.get('stars', 0)}\n"
        f"Weekly entries: {stats.get('weekly_entries', 0)}\n"
        f"Monthly memberships: {stats.get('monthly_members', 0)}\n\n"
        "Use the admin dashboard for full management.",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "⚙️ OPEN ADMIN DASHBOARD",
                    web_app=WebAppInfo(url=ADMIN_DASHBOARD_URL)
                )
            ]
        ]),
    )


# ============================================================
# WEEKLY ADMIN GRANT / REVOKE
# ============================================================

async def grant(update: Update, context):
    uid = update.effective_user.id

    if not is_admin(uid):
        await update.message.reply_text("🚫 Unauthorized.")
        return

    if not context.args:
        await update.message.reply_text(
            "Usage: /grant TELEGRAM_ID"
        )
        return

    target_id = context.args[0]

    if not target_id.isdigit():
        await update.message.reply_text(
            "Invalid Telegram ID."
        )
        return

    success = db.grant_weekly_entry(
        target_id,
        current_weekly_session(),
        uid,
    )

    await update.message.reply_text(
        "✅ Weekly entry granted."
        if success
        else "❌ Could not grant entry."
    )


async def revoke(update: Update, context):
    uid = update.effective_user.id

    if not is_admin(uid):
        await update.message.reply_text("🚫 Unauthorized.")
        return

    if not context.args:
        await update.message.reply_text(
            "Usage: /revoke TELEGRAM_ID"
        )
        return

    target_id = context.args[0]

    if not target_id.isdigit():
        await update.message.reply_text(
            "Invalid Telegram ID."
        )
        return

    success = db.revoke_weekly_entry(
        target_id,
        current_weekly_session(),
    )

    await update.message.reply_text(
        "✅ Weekly entry revoked."
        if success
        else "❌ Entry not found."
    )


# ============================================================
# BAN / UNBAN
# ============================================================

async def ban(update: Update, context):
    uid = update.effective_user.id

    if not is_admin(uid):
        await update.message.reply_text("🚫 Unauthorized.")
        return

    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text(
            "Usage: /ban TELEGRAM_ID"
        )
        return

    target_id = context.args[0]

    db.set_banned(target_id, True, uid)

    await update.message.reply_text(
        f"🚫 Player {target_id} has been banned."
    )


async def unban(update: Update, context):
    uid = update.effective_user.id

    if not is_admin(uid):
        await update.message.reply_text("🚫 Unauthorized.")
        return

    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text(
            "Usage: /unban TELEGRAM_ID"
        )
        return

    target_id = context.args[0]

    db.set_banned(target_id, False, uid)

    await update.message.reply_text(
        f"✅ Player {target_id} has been unbanned."
    )


# ============================================================
# BOT INITIALIZATION
# ============================================================

async def post_init(application: Application):
    logger.info("NEON RUSH ARENA bot initialized")


def build_application():
    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN environment variable is missing."
        )

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("myid", myid))
    application.add_handler(CommandHandler("leaderboard", command_leaderboard))
    application.add_handler(CommandHandler("paysupport", paysupport))
    application.add_handler(CommandHandler("admin", admin))
    application.add_handler(CommandHandler("grant", grant))
    application.add_handler(CommandHandler("revoke", revoke))
    application.add_handler(CommandHandler("ban", ban))
    application.add_handler(CommandHandler("unban", unban))

    application.add_handler(CallbackQueryHandler(buttons))

    application.add_handler(
        PreCheckoutQueryHandler(precheckout)
    )

    application.add_handler(
        MessageHandler(
            filters.SUCCESSFUL_PAYMENT,
            successful_payment,
        )
    )

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            mpesa_phone_message,
        )
    )

    return application


# ============================================================
# RUN BOT
# ============================================================

def run_bot():
    application = build_application()

    application.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    run_bot()

