
import os
import asyncio
import logging

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


BOT_TOKEN = os.getenv("BOT_TOKEN", "")

WEBAPP_URL = os.getenv(
    "WEBAPP_URL",
    "https://dodhiambo26-afk.github.io/neon-rush-game/"
)

ADMIN_DASHBOARD_URL = os.getenv(
    "ADMIN_DASHBOARD_URL",
    "https://dodhiambo26-afk.github.io/neon-rush-game/admin.html"
)

TOURNAMENT_PRICE = 8


# Supports both ADMIN_ID and ADMIN_IDS in Render.
ADMIN_IDS = set()

for env_name in ("ADMIN_ID", "ADMIN_IDS"):
    for value in os.getenv(env_name, "").split(","):
        value = value.strip()
        if value.isdigit():
            ADMIN_IDS.add(int(value))


def is_admin(uid):
    return uid in ADMIN_IDS


logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

logger = logging.getLogger(__name__)


def tournament_url():
    separator = "&" if "?" in WEBAPP_URL else "?"
    return WEBAPP_URL + separator + "mode=tournament"


def free_keyboard(uid=None):
    keyboard = [
        [
            InlineKeyboardButton(
                "🏎 PLAY FREE GAME",
                web_app=WebAppInfo(url=WEBAPP_URL)
            )
        ],
        [
            InlineKeyboardButton(
                "🏆 DAILY TOURNAMENT — 8 ⭐",
                callback_data="tournament"
            )
        ],
        [
            InlineKeyboardButton(
                "📊 LEADERBOARD",
                callback_data="leaderboard"
            ),
            InlineKeyboardButton(
                "👤 PROFILE",
                callback_data="profile"
            )
        ]
    ]

    if uid is not None and is_admin(uid):
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


def tournament_keyboard(uid=None):
    keyboard = [
        [
            InlineKeyboardButton(
                "🏁 ENTER TOURNAMENT",
                web_app=WebAppInfo(url=tournament_url())
            )
        ],
        [
            InlineKeyboardButton(
                "📊 LEADERBOARD",
                callback_data="leaderboard"
            )
        ],
        [
            InlineKeyboardButton(
                "⬅️ MAIN MENU",
                callback_data="menu"
            )
        ]
    ]

    if uid is not None and is_admin(uid):
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
        "🏎 Free Game: Play anytime without paying.\n\n"
        "🏆 Daily Tournament: Pay 8 Telegram Stars "
        "to enter the entire tournament day.\n\n"
        "⏰ Tournament hours: 6:00 AM to 6:00 AM "
        "the following day (Kenya time).\n\n"
        "🥇 Your best single-game score determines "
        "your tournament position.\n\n"
        "Good luck, racer!"
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    db.upsert_user(
        user.id,
        user.username,
        user.first_name,
        user.last_name
    )

    if db.is_banned(user.id):
        await update.message.reply_text(
            "Your account has been blocked."
        )
        return

    await update.message.reply_text(
        main_text(),
        reply_markup=free_keyboard(user.id)
    )


async def send_tournament_invoice(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    user = query.from_user

    session = db.tournament_session()

    if db.has_tournament_entry(user.id, session["key"]):
        await query.message.reply_text(
            "✅ You have already entered today's tournament!\n\n"
            "Your 8-Star entry is valid until 6:00 AM Kenya time.",
            reply_markup=tournament_keyboard(user.id)
        )
        return

    payload = "tournament:" + session["key"]

    await context.bot.send_invoice(
        chat_id=query.message.chat_id,
        title="NEON RUSH Daily Tournament",
        description=(
            "One entry for the complete daily tournament. "
            "Valid until 6:00 AM Kenya time."
        ),
        payload=payload,
        provider_token="",
        currency="XTR",
        prices=[
            LabeledPrice(
                label="Daily Tournament Entry",
                amount=TOURNAMENT_PRICE
            )
        ]
    )


async def leaderboard_message(
    chat_id,
    context,
    uid=None
):
    session = db.tournament_session()

    rows = db.list_top_scores(
        limit=10,
        session_key=session["key"]
    )

    message = (
        "🏆 NEON RUSH DAILY LEADERBOARD\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "Tournament runs 6 AM – 6 AM Kenya time.\n\n"
    )

    if not rows:
        message += (
            "No tournament scores yet. "
            "Be the first racer!\n"
        )
    else:
        medals = ["🥇", "🥈", "🥉"]

        for index, row in enumerate(rows):
            medal = (
                medals[index]
                if index < 3
                else f"{index + 1}."
            )

            message += (
                f"{medal} {row['display_name']} — "
                f"{row['score']} points\n"
            )

    if uid is not None:
        player = db.get_player_ranking(
            uid,
            session["key"]
        )

        message += "\n━━━━━━━━━━━━━━━━━━\n"
        message += "👤 YOUR POSITION\n"

        if player["position"] is None:
            message += (
                "You have not posted a tournament score yet.\n"
            )
        else:
            message += (
                f"Rank: #{player['position']}\n"
                f"Points: {player['score']}\n"
            )

    await context.bot.send_message(
        chat_id=chat_id,
        text=message
    )


async def buttons(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    await query.answer()

    user = query.from_user

    db.upsert_user(
        user.id,
        user.username,
        user.first_name,
        user.last_name
    )

    if db.is_banned(user.id):
        await query.message.reply_text(
            "Your account has been blocked."
        )
        return

    action = query.data

    if action == "menu":
        await query.message.reply_text(
            main_text(),
            reply_markup=free_keyboard(user.id)
        )

    elif action == "tournament":
        session = db.tournament_session()

        if db.has_tournament_entry(user.id, session["key"]):
            await query.message.reply_text(
                "✅ Your tournament entry is active!",
                reply_markup=tournament_keyboard(user.id)
            )
        else:
            await send_tournament_invoice(update, context)

    elif action == "leaderboard":
        await leaderboard_message(
            query.message.chat_id,
            context,
            user.id
        )

    elif action == "profile":
        session = db.tournament_session()

        entry = db.has_tournament_entry(
            user.id,
            session["key"]
        )

        player = db.get_player_ranking(
            user.id,
            session["key"]
        )

        name = user.full_name

        text = (
            "👤 YOUR NEON RUSH PROFILE\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"Name: {name}\n"
            f"Telegram ID: {user.id}\n\n"
            f"Tournament entry: "
            f"{'ACTIVE' if entry else 'NOT ENTERED'}\n"
            f"Best tournament score: {player['score']}\n"
        )

        if player["position"]:
            text += f"Your position: #{player['position']}\n"

        await query.message.reply_text(
            text,
            reply_markup=free_keyboard(user.id)
        )


async def precheckout(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.pre_checkout_query

    session = db.tournament_session()
    expected_payload = "tournament:" + session["key"]

    if query.invoice_payload != expected_payload:
        await query.answer(
            ok=False,
            error_message=(
                "This tournament invoice has expired. "
                "Please request a new entry."
            )
        )
        return

    if (
        query.currency != "XTR"
        or query.total_amount != TOURNAMENT_PRICE
    ):
        await query.answer(
            ok=False,
            error_message="Invalid tournament payment amount."
        )
        return

    if db.is_banned(query.from_user.id):
        await query.answer(
            ok=False,
            error_message=(
                "Your account cannot enter this tournament."
            )
        )
        return

    await query.answer(ok=True)


async def successful_payment(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    user = update.effective_user
    payment = update.message.successful_payment

    session = db.tournament_session()
    expected_payload = "tournament:" + session["key"]

    if payment.invoice_payload != expected_payload:
        await update.message.reply_text(
            "Payment received, but the tournament session has changed. "
            "Please contact support."
        )
        return

    if (
        payment.currency != "XTR"
        or payment.total_amount != TOURNAMENT_PRICE
    ):
        await update.message.reply_text(
            "The payment amount could not be verified. "
            "Please contact support."
        )
        return

    inserted = db.record_tournament_payment(
        uid=user.id,
        charge_id=payment.telegram_payment_charge_id,
        provider_id=payment.provider_payment_charge_id,
        amount=payment.total_amount,
        currency=payment.currency
    )

    session = db.tournament_session()

    if db.has_tournament_entry(user.id, session["key"]):
        await update.message.reply_text(
            "🎉 PAYMENT SUCCESSFUL!\n\n"
            "Your 8-Star tournament entry is active.\n"
            "You can play as many races as you want during this "
            "tournament day.\n\n"
            "Your best single-game score counts on the leaderboard.\n"
            "Your entry ends at 6:00 AM Kenya time.",
            reply_markup=tournament_keyboard(user.id)
        )

    elif not inserted:
        await update.message.reply_text(
            "This payment was already processed. "
            "If you need help, contact support."
        )

    else:
        await update.message.reply_text(
            "Payment was received, but entry activation needs attention. "
            "Please contact support."
        )


async def myid(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    await update.message.reply_text(
        f"Your Telegram user ID is: {update.effective_user.id}"
    )


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

    stats = db.get_stats()

    await update.message.reply_text(
        "⚙️ NEON RUSH ADMIN\n\n"
        f"Players: {stats['players']}\n"
        f"Payments: {stats['payments']}\n"
        f"Stars received: {stats['stars']}\n"
        f"Tournament entries: {stats['tournament_entries']}\n\n"
        "Use the secure dashboard for full administration.",
        reply_markup=admin_keyboard()
    )


async def grant(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not is_admin(update.effective_user.id):
        return

    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text(
            "Usage: /grant TELEGRAM_USER_ID"
        )
        return

    uid = int(context.args[0])

    db.grant_tournament_entry(uid)

    await update.message.reply_text(
        f"✅ Tournament access granted to {uid} "
        "for the current tournament day."
    )


async def revoke(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not is_admin(update.effective_user.id):
        return

    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text(
            "Usage: /revoke TELEGRAM_USER_ID"
        )
        return

    uid = int(context.args[0])
    session = db.tournament_session()

    with db._lock:
        con = db.connect()

        con.execute("""
            DELETE FROM tournament_entries
            WHERE telegram_id=? AND session_key=?
        """, (uid, session["key"]))

        con.commit()
        con.close()

    await update.message.reply_text(
        f"Current tournament access revoked for {uid}."
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


async def paysupport(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    await update.message.reply_text(
        "For payment assistance, contact the NEON RUSH administrator."
    )


async def post_init(application: Application):
    await application.bot.set_my_commands([
        ("start", "Open NEON RUSH"),
        ("leaderboard", "View today's leaderboard"),
        ("myid", "Show your Telegram user ID"),
        ("paysupport", "Payment assistance"),
        ("admin", "Open administrator dashboard")
    ])


async def command_leaderboard(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    await leaderboard_message(
        update.effective_chat.id,
        context,
        update.effective_user.id
    )


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
    application.add_handler(
        CommandHandler("leaderboard", command_leaderboard)
    )
    application.add_handler(CommandHandler("myid", myid))
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
            successful_payment
        )
    )

    return application


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
