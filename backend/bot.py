
import os
import asyncio
import logging

from datetime import datetime, timezone

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    WebAppInfo,
    LabeledPrice
)

from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    PreCheckoutQueryHandler,
    MessageHandler,
    ContextTypes,
    filters
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

ADMIN_DASHBOARD_URL = os.getenv(
    "ADMIN_DASHBOARD_URL",
    "https://dodhiambo26-afk.github.io/neon-rush-game/admin.html"
)

MONTHLY_PRICE = 300

SUPPORT_WHATSAPP = "+254183375330"
SUPPORT_EMAIL = "neonrusharena.com"


ADMIN_IDS = set()

for env_name in ("ADMIN_ID", "ADMIN_IDS"):
    for value in os.getenv(env_name, "").split(","):
        value = value.strip()

        if value.isdigit():
            ADMIN_IDS.add(int(value))


logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

logger = logging.getLogger(__name__)


# ============================================================
# GENERAL HELPERS
# ============================================================

def is_admin(uid):
    return int(uid) in ADMIN_IDS


def game_url(mode):
    separator = "&" if "?" in WEBAPP_URL else "?"

    return (
        WEBAPP_URL
        + separator
        + "mode="
        + mode
    )


def get_monthly_session():
    return db.monthly_session()


def get_weekly_session():
    return db.weekly_session()


def get_contact_text():
    whatsapp = db.get_setting(
        "support_whatsapp",
        SUPPORT_WHATSAPP
    )

    email = db.get_setting(
        "support_email",
        SUPPORT_EMAIL
    )

    return (
        "\n\n📞 SUPPORT\n"
        f"WhatsApp: {whatsapp}\n"
        f"Email: {email}"
    )


def player_keyboard(uid):
    keyboard = [
        [
            InlineKeyboardButton(
                "🏎 PLAY FREE GAME",
                web_app=WebAppInfo(url=game_url("free"))
            )
        ],
        [
            InlineKeyboardButton(
                "🏆 WEEKLY TOURNAMENT",
                callback_data="weekly"
            )
        ],
        [
            InlineKeyboardButton(
                "💎 MONTHLY PREMIUM — 300 ⭐",
                callback_data="monthly"
            )
        ],
        [
            InlineKeyboardButton(
                "📊 WEEKLY LEADERBOARD",
                callback_data="weekly_leaderboard"
            )
        ],
        [
            InlineKeyboardButton(
                "📊 MONTHLY LEADERBOARD",
                callback_data="monthly_leaderboard"
            )
        ],
        [
            InlineKeyboardButton(
                "👤 MY PROFILE",
                callback_data="profile"
            )
        ]
    ]

    if is_admin(uid):
        keyboard.append([
            InlineKeyboardButton(
                "⚙️ ADMIN DASHBOARD",
                web_app=WebAppInfo(url=ADMIN_DASHBOARD_URL)
            )
        ])

    return InlineKeyboardMarkup(keyboard)


def admin_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "⚙️ OPEN ADMIN DASHBOARD",
                web_app=WebAppInfo(url=ADMIN_DASHBOARD_URL)
            )
        ]
    ])


def tournament_keyboard(uid, mode):
    if mode == "weekly":
        url = game_url("weekly")
        label = "🏁 PLAY WEEKLY TOURNAMENT"

    else:
        url = game_url("monthly")
        label = "🏁 PLAY MONTHLY TOURNAMENT"

    keyboard = [
        [
            InlineKeyboardButton(
                label,
                web_app=WebAppInfo(url=url)
            )
        ],
        [
            InlineKeyboardButton(
                "📊 VIEW LEADERBOARD",
                callback_data=mode + "_leaderboard"
            )
        ],
        [
            InlineKeyboardButton(
                "⬅️ MAIN MENU",
                callback_data="menu"
            )
        ]
    ]

    if is_admin(uid):
        keyboard.append([
            InlineKeyboardButton(
                "⚙️ ADMIN DASHBOARD",
                web_app=WebAppInfo(url=ADMIN_DASHBOARD_URL)
            )
        ])

    return InlineKeyboardMarkup(keyboard)


def main_text():
    return (
        "⚡ NEON RUSH ⚡\n\n"
        "Welcome to the racing arena!\n\n"

        "🏎 FREE GAME\n"
        "Play anytime without paying. Choose your difficulty level inside the game.\n\n"

        "🏆 WEEKLY TOURNAMENT\n"
        "PAY KES 25 to enter. Players are admitted manually by the administrator.\n"
        "Runs Sunday 6:00 PM to the following Sunday 6:00 PM, Kenya time.\n"
        "Top 5 players receive rewards.\n\n"

        "💎 MONTHLY PREMIUM\n"
        "300 Telegram Stars per calendar month.\n"
        "Access ends at the end of that calendar month.\n"
        "Top 5 players receive rewards.\n\n"

        "Good luck, racer!"
        + get_contact_text()
    )


async def ensure_user(user):
    db.upsert_user(
        user.id,
        user.username,
        user.first_name,
        user.last_name
    )


async def reject_banned(message, uid):
    if db.is_banned(uid):
        await message.reply_text(
            "🚫 Your account has been blocked. "
            "Please contact support."
            + get_contact_text()
        )

        return True

    return False


# ============================================================
# START COMMAND
# ============================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    user = update.effective_user

    await ensure_user(user)

    if await reject_banned(update.message, user.id):
        return

    await update.message.reply_text(
        main_text(),
        reply_markup=player_keyboard(user.id)
    )


# ============================================================
# WEEKLY TOURNAMENT
# ============================================================

async def weekly_information(
    chat_id,
    context,
    uid
):
    session = get_weekly_session()

    has_access = db.has_weekly_entry(
        uid,
        session["key"]
    )

    locked = db.is_weekly_locked()

    if has_access:
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "✅ WEEKLY TOURNAMENT ACCESS ACTIVE!\n\n"
                "You have been admitted by the administrator.\n\n"
                "Your best single-game score determines your position.\n"
                "The Top 5 players receive rewards.\n\n"
                "Tournament schedule:\n"
                "Sunday 6:00 PM – Sunday 6:00 PM (Kenya time)."
            ),
            reply_markup=tournament_keyboard(uid, "weekly")
        )

    else:
        status = (
            "🔒 Tournament admission is currently locked."
            if locked
            else "Admission is controlled by the administrator."
        )

        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "🏆 NEON RUSH WEEKLY TOURNAMENT\n\n"
                "Entry is KES 25.\n\n"
                "Players must be admitted manually by the administrator.\n\n"
                + status
                + "\n\n"
                "Please contact the administrator to request admission."
                + get_contact_text()
            ),
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "⬅️ MAIN MENU",
                        callback_data="menu"
                    )
                ]
            ])
        )


# ============================================================
# MONTHLY PREMIUM PAYMENT
# ============================================================

async def send_monthly_invoice(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    user = query.from_user

    session = get_monthly_session()

    if db.has_monthly_access(user.id, session["key"]):
        await query.message.reply_text(
            "✅ YOUR MONTHLY PREMIUM MEMBERSHIP IS ACTIVE!\n\n"
            f"Month: {session['key']}\n"
            f"Access expires: {session['end']}\n\n"
            "You can play the monthly tournament until the end "
            "of this calendar month.",
            reply_markup=tournament_keyboard(user.id, "monthly")
        )

        return

    payload = "monthly:" + session["key"]

    await context.bot.send_invoice(
        chat_id=query.message.chat_id,
        title="NEON RUSH Monthly Premium",
        description=(
            "Monthly premium tournament access. "
            "Includes access until the end of the current calendar month. "
            "Top 5 players receive rewards."
        ),
        payload=payload,
        provider_token="",
        currency="XTR",
        prices=[
            LabeledPrice(
                label="Monthly Premium Membership",
                amount=MONTHLY_PRICE
            )
        ]
    )


# ============================================================
# SEPARATE LEADERBOARDS
# ============================================================

async def leaderboard_message(
    chat_id,
    context,
    uid,
    mode
):
    if mode == "weekly":
        session = get_weekly_session()
        title = "WEEKLY"

        has_access = db.has_weekly_entry(
            uid,
            session["key"]
        )

    else:
        session = get_monthly_session()
        title = "MONTHLY PREMIUM"

        has_access = db.has_monthly_access(
            uid,
            session["key"]
        )

    rows = db.list_tournament_scores(
        mode=mode,
        session_key=session["key"],
        limit=10
    )

    message = (
        f"🏆 NEON RUSH {title} LEADERBOARD\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"Period: {session['key']}\n"
        "Top 5 players receive rewards.\n\n"
    )

    if not rows:
        message += (
            "No scores have been recorded yet.\n"
            "Be the first racer!\n"
        )

    else:
        medals = ["🥇", "🥈", "🥉", "4.", "5."]

        for index, row in enumerate(rows):
            position = (
                medals[index]
                if index < 5
                else f"{index + 1}."
            )

            message += (
                f"{position} {row['display_name']} — "
                f"{row['score']} points\n"
            )

    message += "\n━━━━━━━━━━━━━━━━━━\n"

    if has_access:
        player = db.get_tournament_ranking(
            uid,
            mode,
            session["key"]
        )

        message += "👤 YOUR POSITION\n"

        if player["position"] is None:
            message += "You have not posted a score yet.\n"

        else:
            message += (
                f"Rank: #{player['position']}\n"
                f"Points: {player['score']}\n"
            )

    else:
        message += (
            "You do not currently have access to this tournament.\n"
        )

    await context.bot.send_message(
        chat_id=chat_id,
        text=message,
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "⬅️ MAIN MENU",
                    callback_data="menu"
                )
            ]
        ])
    )


# ============================================================
# PLAYER PROFILE
# ============================================================

async def profile_message(
    chat_id,
    context,
    user
):
    weekly = get_weekly_session()
    monthly = get_monthly_session()

    weekly_access = db.has_weekly_entry(
        user.id,
        weekly["key"]
    )

    monthly_access = db.has_monthly_access(
        user.id,
        monthly["key"]
    )

    weekly_rank = db.get_tournament_ranking(
        user.id,
        "weekly",
        weekly["key"]
    )

    monthly_rank = db.get_tournament_ranking(
        user.id,
        "monthly",
        monthly["key"]
    )

    message = (
        "👤 YOUR NEON RUSH PROFILE\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"Name: {user.full_name}\n"
        f"Telegram ID: {user.id}\n\n"

        "🏆 WEEKLY TOURNAMENT\n"
        f"Access: {'ACTIVE' if weekly_access else 'NOT ADMITTED'}\n"
        f"Best score: {weekly_rank['score']}\n"
    )

    if weekly_access and weekly_rank["position"]:
        message += (
            f"Position: #{weekly_rank['position']}\n"
        )

    message += (
        "\n💎 MONTHLY PREMIUM\n"
        f"Membership: {'ACTIVE' if monthly_access else 'INACTIVE'}\n"
        f"Current month: {monthly['key']}\n"
    )

    if monthly_access:
        message += (
            f"Expires: {monthly['end']}\n"
            f"Best score: {monthly_rank['score']}\n"
        )

        if monthly_rank["position"]:
            message += (
                f"Position: #{monthly_rank['position']}\n"
            )

    message += get_contact_text()

    await context.bot.send_message(
        chat_id=chat_id,
        text=message,
        reply_markup=player_keyboard(user.id)
    )


# ============================================================
# BUTTON HANDLER
# ============================================================

async def buttons(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    await query.answer()

    user = query.from_user

    await ensure_user(user)

    if db.is_banned(user.id):
        await query.message.reply_text(
            "🚫 Your account has been blocked."
            + get_contact_text()
        )

        return

    action = query.data

    if action == "menu":
        await query.message.reply_text(
            main_text(),
            reply_markup=player_keyboard(user.id)
        )

    elif action == "weekly":
        await weekly_information(
            query.message.chat_id,
            context,
            user.id
        )

    elif action == "monthly":
        await send_monthly_invoice(
            update,
            context
        )

    elif action == "weekly_leaderboard":
        await leaderboard_message(
            query.message.chat_id,
            context,
            user.id,
            "weekly"
        )

    elif action == "monthly_leaderboard":
        await leaderboard_message(
            query.message.chat_id,
            context,
            user.id,
            "monthly"
        )

    elif action == "profile":
        await profile_message(
            query.message.chat_id,
            context,
            user
        )


# ============================================================
# PAYMENT PRE-CHECKOUT
# ============================================================

async def precheckout(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.pre_checkout_query

    session = get_monthly_session()
    expected_payload = "monthly:" + session["key"]

    if query.invoice_payload != expected_payload:
        await query.answer(
            ok=False,
            error_message=(
                "This monthly invoice has expired. "
                "Please request a new invoice."
            )
        )

        return

    if (
        query.currency != "XTR"
        or query.total_amount != MONTHLY_PRICE
    ):
        await query.answer(
            ok=False,
            error_message="Invalid monthly membership payment."
        )

        return

    if db.is_banned(query.from_user.id):
        await query.answer(
            ok=False,
            error_message="Your account cannot purchase membership."
        )

        return

    await query.answer(ok=True)


# ============================================================
# SUCCESSFUL MONTHLY PAYMENT
# ============================================================

async def successful_payment(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    user = update.effective_user
    payment = update.message.successful_payment

    await ensure_user(user)

    session = get_monthly_session()
    expected_payload = "monthly:" + session["key"]

    if payment.invoice_payload != expected_payload:
        await update.message.reply_text(
            "Payment received, but this monthly session has changed. "
            "Please contact support."
            + get_contact_text()
        )

        return

    if (
        payment.currency != "XTR"
        or payment.total_amount != MONTHLY_PRICE
    ):
        await update.message.reply_text(
            "The payment amount could not be verified. "
            "Please contact support."
        )

        return

    con = db.connect()

    try:
        con.execute("BEGIN IMMEDIATE")

        cursor = con.execute("""
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
            user.id,
            payment.telegram_payment_charge_id,
            payment.provider_payment_charge_id,
            payment.total_amount,
            payment.currency,
            datetime.now(timezone.utc).isoformat()
        ))

        if cursor.rowcount == 0:
            con.rollback()

            await update.message.reply_text(
                "This payment has already been processed. "
                "If your membership is not active, please contact support."
                + get_contact_text()
            )

            return

        payment_id = cursor.lastrowid

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
                starts_at=excluded.starts_at,
                expires_at=excluded.expires_at
        """, (
            user.id,
            session["key"],
            payment_id,
            session["start"],
            session["end"],
            datetime.now(timezone.utc).isoformat()
        ))

        con.execute("""
            UPDATE users
            SET premium_until=?
            WHERE telegram_id=?
        """, (
            session["end"],
            user.id
        ))

        con.commit()

    except Exception:
        con.rollback()
        logger.exception("Monthly payment processing failed")

        await update.message.reply_text(
            "Payment was received, but membership activation needs attention. "
            "Please contact support."
            + get_contact_text()
        )

        return

    finally:
        con.close()

    await update.message.reply_text(
        "🎉 MONTHLY PREMIUM PAYMENT SUCCESSFUL!\n\n"
        "Your 300-Star membership is now active.\n\n"
        f"Membership month: {session['key']}\n"
        f"Expiry: {session['end']}\n\n"
        "You can now play the monthly premium tournament.\n"
        "Your best single-game score counts on the leaderboard.\n\n"
        "Good luck!"
        + get_contact_text(),
        reply_markup=tournament_keyboard(
            user.id,
            "monthly"
        )
    )


# ============================================================
# BASIC COMMANDS
# ============================================================

async def myid(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    await update.message.reply_text(
        f"Your Telegram user ID is: {update.effective_user.id}"
    )


async def command_leaderboard(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    mode = "weekly"

    if context.args:
        requested = context.args[0].lower()

        if requested in ("weekly", "monthly"):
            mode = requested

    user = update.effective_user

    await ensure_user(user)

    await leaderboard_message(
        update.effective_chat.id,
        context,
        user.id,
        mode
    )


async def paysupport(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    await update.message.reply_text(
        "📞 NEON RUSH PAYMENT SUPPORT\n\n"
        "For payment or membership assistance, contact:\n"
        + get_contact_text()
    )


# ============================================================
# ADMIN COMMANDS
# ============================================================

async def admin(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    uid = update.effective_user.id

    if not is_admin(uid):
        await update.message.reply_text(
            "⛔ You are not authorized to access the admin panel."
        )

        return

    con = db.connect()

    try:
        players = con.execute(
            "SELECT COUNT(*) FROM users"
        ).fetchone()[0]

        payments = con.execute(
            "SELECT COUNT(*) FROM payments"
        ).fetchone()[0]

        stars = con.execute("""
            SELECT COALESCE(SUM(amount), 0)
            FROM payments
            WHERE currency='XTR'
        """).fetchone()[0]

        weekly = get_weekly_session()

        entries = con.execute("""
            SELECT COUNT(*)
            FROM weekly_entries
            WHERE session_key=?
        """, (weekly["key"],)).fetchone()[0]

    finally:
        con.close()

    await update.message.reply_text(
        "⚙️ NEON RUSH ADMIN\n\n"
        f"Registered players: {players}\n"
        f"Payment records: {payments}\n"
        f"Telegram Stars received: {stars}\n"
        f"Weekly tournament entries: {entries}\n\n"
        "Use the secure dashboard for full administration.",
        reply_markup=admin_keyboard()
    )


async def grant(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    admin_id = update.effective_user.id

    if not is_admin(admin_id):
        return

    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text(
            "Usage: /grant TELEGRAM_USER_ID"
        )

        return

    uid = int(context.args[0])

    if not db.get_user(uid):
        await update.message.reply_text(
            "Player not found. Ask them to open the game first."
        )

        return

    session = get_weekly_session()

    if db.has_weekly_entry(uid, session["key"]):
        await update.message.reply_text(
            "This player already has weekly access."
        )

        return

    db.grant_weekly_entry(
        uid,
        session["key"],
        admin_id
    )

    await update.message.reply_text(
        f"✅ Weekly tournament access granted to {uid}."
    )


async def revoke(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    admin_id = update.effective_user.id

    if not is_admin(admin_id):
        return

    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text(
            "Usage: /revoke TELEGRAM_USER_ID"
        )

        return

    uid = int(context.args[0])
    session = get_weekly_session()

    removed = db.revoke_weekly_entry(
        uid,
        session["key"]
    )

    await update.message.reply_text(
        (
            f"✅ Weekly access revoked for {uid}."
            if removed
            else "No weekly entry was found for this player."
        )
    )


async def ban(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not is_admin(update.effective_user.id):
        return

    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text(
            "Usage: /ban TELEGRAM_USER_ID"
        )

        return

    uid = int(context.args[0])

    if is_admin(uid):
        await update.message.reply_text(
            "You cannot ban a configured administrator."
        )

        return

    db.set_banned(uid, True)

    await update.message.reply_text(
        f"🚫 User {uid} has been blocked."
    )


async def unban(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not is_admin(update.effective_user.id):
        return

    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text(
            "Usage: /unban TELEGRAM_USER_ID"
        )

        return

    uid = int(context.args[0])

    db.set_banned(uid, False)

    await update.message.reply_text(
        f"✅ User {uid} has been unblocked."
    )


# ============================================================
# BOT COMMAND MENU
# ============================================================

async def post_init(application: Application):
    await application.bot.set_my_commands([
        ("start", "Open NEON RUSH"),
        ("leaderboard", "View weekly leaderboard"),
        ("myid", "Show your Telegram user ID"),
        ("paysupport", "Payment assistance"),
        ("admin", "Open administrator dashboard"),
        ("grant", "Admit a player to the weekly tournament"),
        ("revoke", "Remove weekly tournament access"),
        ("ban", "Block a player"),
        ("unban", "Unblock a player")
    ])


# ============================================================
# BUILD APPLICATION
# ============================================================

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

    application.add_handler(
        CommandHandler("start", start)
    )

    application.add_handler(
        CommandHandler("leaderboard", command_leaderboard)
    )

    application.add_handler(
        CommandHandler("myid", myid)
    )

    application.add_handler(
        CommandHandler("paysupport", paysupport)
    )

    application.add_handler(
        CommandHandler("admin", admin)
    )

    application.add_handler(
        CommandHandler("grant", grant)
    )

    application.add_handler(
        CommandHandler("revoke", revoke)
    )

    application.add_handler(
        CommandHandler("ban", ban)
    )

    application.add_handler(
        CommandHandler("unban", unban)
    )

    application.add_handler(
        CallbackQueryHandler(buttons)
    )

    application.add_handler(
        PreCheckoutQueryHandler(precheckout)
    )

    application.add_handler(
        MessageHandler(
            filters.SUCCESSFUL_PAYMENT,
            successful_payment
        )
    )

    return application


# ============================================================
# RUN BOT
# ============================================================

async def run_bot():
    application = build_application()

    await application.initialize()
    await application.start()

    if application.updater:
        await application.updater.start_polling()

    logger.info("NEON RUSH Telegram bot is running.")

    try:
        await asyncio.Event().wait()

    finally:
        if application.updater:
            await application.updater.stop()

        await application.stop()
        await application.shutdown()
