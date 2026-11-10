import os
import asyncio
import logging

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


# ============================================================
# CONFIGURATION
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "")

WEBAPP_URL = os.getenv(
    "WEBAPP_URL",
    "https://dodhiambo26-afk.github.io/neon-rush-game/"
)
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
                "👑 MONTHLY TOURNAMENT — 300 STARS",
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
    name = user.first_name or "Player"

    return (
        f"🚀 Welcome to NEON RUSH ARENA, {name}!\n\n"
        "Choose your game mode:\n\n"
        "🎮 FREE DAILY GAME\n"
        "Play free every day.\n\n"
        "🏆 WEEKLY TOURNAMENT\n"
        "Entry: KES 25\n"
        "Entry is approved by an administrator.\n"
        "Weekly session: Sunday 6 PM to Sunday 6 PM "
        "(Kenya time).\n"
        "Compete for Weekly leaderboard rewards.\n\n"
        "🌟 MONTHLY TOURNAMENT\n"
        "Membership: 300 Telegram Stars.\n"
        "Compete for Monthly leaderboard rewards.\n\n"
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
        "🏆 WEEKLY TOURNAMENT\n\n"
        "Entry fee: KES 25\n"
        "Entries are approved manually by an administrator.\n\n"
        "Session: Sunday 6 PM to Sunday 6 PM Kenya time.\n\n"
        "Contact support or an administrator for entry approval.",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "📞 SUPPORT",
                    callback_data="support"
                )
            ]
        ]),
    )


# ============================================================
# MONTHLY TOURNAMENT
# ============================================================

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


# ============================================================
# LEADERBOARDS
# ============================================================

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

    lines = [title, ""]

    for position, item in enumerate(scores, start=1):
        username = item.get("username") or item.get("telegram_id")
        score = item.get("score", 0)

        lines.append(
            f"{position}. {username} — {score} points"
        )

    await query.message.reply_text("\n".join(lines))


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

    weekly_access = db.has_weekly_entry(
        uid,
        current_weekly_session()
    )

    monthly_access = db.has_monthly_access(
        uid,
        current_month_session()
    )

    await query.message.reply_text(
        "👤 PLAYER PROFILE\n\n"
        f"Telegram ID: {uid}\n"
        f"Username: @{user.get('username') or 'Not set'}\n\n"
        f"Weekly tournament access: "
        f"{'✅ Active' if weekly_access else '❌ Not active'}\n"
        f"Monthly tournament access: "
        f"{'✅ Active' if monthly_access else '❌ Not active'}"
    )


# ============================================================
# CALLBACK BUTTONS
# ============================================================

async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    if query.data == "weekly_info":
        await weekly_information(update, context)

    elif query.data == "monthly_join":
        await send_monthly_invoice(update, context)

    elif query.data == "weekly_leaderboard":
        await leaderboard_message(update, context, "weekly")

    elif query.data == "monthly_leaderboard":
        await leaderboard_message(update, context, "monthly")

    elif query.data == "profile":
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
        await query.answer(
            ok=False,
            error_message="Your account is suspended.",
        )
        return

    expected_payload = (
        f"monthly_membership_{current_month_session()}"
    )

    if query.invoice_payload != expected_payload:
        await query.answer(
            ok=False,
            error_message="This membership invoice has expired.",
        )
        return

    if query.currency != MONTHLY_CURRENCY:
        await query.answer(
            ok=False,
            error_message="Invalid payment currency.",
        )
        return

    if query.total_amount != MONTHLY_PRICE:
        await query.answer(
            ok=False,
            error_message="Invalid payment amount.",
        )
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

    if payment.currency != MONTHLY_CURRENCY:
        await update.message.reply_text(
            "❌ Invalid payment currency."
        )
        return

    if payment.total_amount != MONTHLY_PRICE:
        await update.message.reply_text(
            "❌ Invalid payment amount."
        )
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

