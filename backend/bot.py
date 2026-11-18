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
import notifications as notification_service
import mega_tournament
import capacity_tournaments
from mpesa import initiate_stk_push
from paypal_payments import create_paypal_order


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

    return db.upsert_user(
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
            InlineKeyboardButton("🏆 10,000-PLAYER TOURNAMENT", callback_data="capacity_info_tournament_10k")
        ],
        [
            InlineKeyboardButton("🏆 1,000-PLAYER TOURNAMENT", callback_data="capacity_info_tournament_1k")
        ],
        [
            InlineKeyboardButton(
                "🏆 100K MEGA TOURNAMENT — KSh 35",
                callback_data="mega_info"
            )
        ],
        [
            InlineKeyboardButton("📊 10K LEADERBOARD", callback_data="capacity_leaderboard_tournament_10k"),
            InlineKeyboardButton("📊 1K LEADERBOARD", callback_data="capacity_leaderboard_tournament_1k")
        ],
        [
            InlineKeyboardButton(
                "🔔 NOTIFICATION SETTINGS",
                callback_data="alerts_open"
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
        "Membership: KSh 200 by M-Pesa or 300 Telegram Stars.\n\n"
        "🏆 100K MEGA TOURNAMENT\n"
        "Entry: KSh 35 by M-Pesa. Starts automatically at 100,000 confirmed paid members.\n"
        "\n<b>━━━━━━━━━━━━━━━━━━━━</b>\n"
        "🏆 <b>TOP 5 PLAYERS WIN REWARDS!</b> 🏆\n"
        "<b>In EVERY monthly tournament.</b>\n"
        "<b>━━━━━━━━━━━━━━━━━━━━</b>\n"
        "<i>Final standings are reviewed before awards are confirmed. Cash payouts are handled separately and are not automatic.</i>\n\n"
        "Use /alerts or the notification settings button to control your alerts.\n\n"
        "Good luck, Racer!"
    )


# ============================================================
# START
# ============================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    user_record = ensure_user(update)

    # upsert_user already returns the current user record, including banned status.
    # Reuse it instead of making a second Firestore read for every /start.
    if user_record and user_record.get("banned", False):
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
        "Entry fee: KSh 25 by M-Pesa, US$0.80 by PayPal, OR 50 Telegram Stars.\n\n"
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
            [InlineKeyboardButton("🌍 PAY US$0.80 WITH PAYPAL", callback_data="weekly_paypal")],
            [InlineKeyboardButton("⭐ PAY 50 TELEGRAM STARS", callback_data="weekly_stars")],
            [InlineKeyboardButton("📞 SUPPORT", callback_data="support")],
        ]),
    )


async def send_mega_stars_invoice(update, context):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    if reject_banned(uid):
        await query.message.reply_text("🚫 Your account has been suspended.")
        return
    campaign = mega_tournament.get_status()
    if campaign.get("status") == "finished":
        await query.message.reply_text("⛔ The 100K Mega Tournament is closed.")
        return
    if mega_tournament.has_access(uid):
        await query.message.reply_text("✅ Your Mega Tournament membership is already confirmed.")
        return
    await context.bot.send_invoice(
        chat_id=uid,
        title="NEON RUSH ARENA 100K Mega Tournament Entry",
        description="One-time entry to the 100K Mega Tournament. Compete for the official top-five cash prizes.",
        payload="mega_tournament_50_stars_v1",
        currency="XTR",
        prices=[LabeledPrice(label="100K Mega Tournament Entry", amount=50)],
        provider_token="",
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
        "• US$2.00 via PayPal (about KSh 260)\n"
        "• 300 Telegram Stars\n\n"
        "\n━━━━━━━━━━━━━━━━━━━━\n"
        "🏆 <b>TOP 5 PLAYERS WIN REWARDS!</b> 🏆\n"
        "<b>Every monthly tournament.</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<i>Final standings are reviewed before awards are confirmed. Cash payouts are not automatic.</i>",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📱 PAY KSh 200 WITH M-PESA", callback_data="monthly_mpesa")],
            [InlineKeyboardButton("🌍 PAY US$2.00 WITH PAYPAL", callback_data="monthly_paypal")],
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

async def capacity_tournament_information(update, context, kind):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    if reject_banned(uid):
        await query.message.reply_text("🚫 Your account has been suspended.")
        return
    cfg = capacity_tournaments.TOURNAMENTS[kind]
    status = capacity_tournaments.get_status(kind)
    prizes = "\n".join(f"{p['position']}. KSh {p['amount_kes']:,}" for p in cfg["prizes"])
    total = int(status.get("total_members", 0))
    text = (
        f"🏆 {cfg['name'].upper()}\n\n"
        "Entry: KSh 50 by M-Pesa, US$0.80 by PayPal, or 50 Telegram Stars.\n"
        f"Admitted: {total:,} / {cfg['target']:,}\n"
        f"Paid: {int(status.get('confirmed_members', 0)):,} | Complimentary: {int(status.get('complimentary_members', 0)):,}\n"
        "Starts automatically when the target is reached. Complimentary entries count toward capacity and are prize eligible.\n\n"
        "🏅 TOP FIVE PRIZES\n" + prizes + "\n\n"
        "Final standings are reviewed before awards are confirmed. Cash payouts are handled separately and are not automatic."
    )
    buttons = []
    if status.get("status") == "waiting" and not capacity_tournaments.has_access(kind, uid):
        buttons.extend([
            [InlineKeyboardButton("📱 PAY KSh 50 WITH M-PESA", callback_data=f"capacity_mpesa_{kind}")],
            [InlineKeyboardButton("🌍 PAY US$0.80 WITH PAYPAL", callback_data=f"capacity_paypal_{kind}")],
            [InlineKeyboardButton("⭐ PAY 50 TELEGRAM STARS", callback_data=f"capacity_stars_{kind}")],
        ])
    elif capacity_tournaments.has_access(kind, uid):
        text += "\n\n✅ Your entry is confirmed."
        if status.get("status") == "active":
            buttons.append([InlineKeyboardButton("🏁 PLAY TOURNAMENT", web_app=WebAppInfo(url=game_url(kind)))])
    await query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons) if buttons else None)


async def send_capacity_stars_invoice(update, context, kind):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    if reject_banned(uid):
        await query.message.reply_text("🚫 Your account has been suspended.")
        return
    cfg = capacity_tournaments.TOURNAMENTS[kind]
    status = capacity_tournaments.get_status(kind)
    if status.get("status") != "waiting" or capacity_tournaments.has_access(kind, uid):
        await query.message.reply_text("This tournament is closed or your entry is already confirmed.")
        return
    await context.bot.send_invoice(
        chat_id=uid, title=cfg["name"], description="Tournament entry. Top-five prizes apply.",
        payload=f"{kind}_50_stars_v1", currency="XTR",
        prices=[LabeledPrice(label=cfg["name"] + " entry", amount=50)], provider_token="",
    )


async def capacity_leaderboard_message(update, context, kind):
    query = update.callback_query
    await query.answer()
    if reject_banned(query.from_user.id):
        await query.message.reply_text("🚫 Your account has been suspended.")
        return
    scores = capacity_tournaments.leaderboard(kind, 10)
    cfg = capacity_tournaments.TOURNAMENTS[kind]
    lines = [f"🏆 {cfg['name'].upper()} LEADERBOARD", ""]
    if not scores:
        lines.append("No verified scores have been recorded yet.")
    for position, item in enumerate(scores, 1):
        name = html.escape(str(item.get("username") or item.get("telegram_id")))
        lines.append(f"{position}. {name} — {int(item.get('score', 0)):,} points")
    await query.message.reply_text("\n".join(lines), parse_mode="HTML")


async def mega_tournament_information(update, context):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    if reject_banned(uid):
        await query.message.reply_text("🚫 Your account has been suspended.")
        return
    campaign = mega_tournament.get_status()
    prizes = "\n".join(
        f"{p['position']}. KSh {p['amount_kes']:,}"
        for p in campaign.get("prizes", mega_tournament.PRIZES)
    )
    status = str(campaign.get("status", "waiting")).upper()
    count = int(campaign.get("confirmed_members", 0))
    text = (
        "🏆 NEON RUSH 100K MEGA TOURNAMENT\n\n"
        "Entry: KSh 35 by M-Pesa, US$0.80 by PayPal, or 50 Telegram Stars.\n"
        f"Total admitted players: {int(campaign.get('total_members', count + int(campaign.get('complimentary_members', 0)))):,} / 100,000\n"
        f"Paid entries: {count:,} | Complimentary: {int(campaign.get('complimentary_members', 0)):,}\n"
        "The tournament starts automatically when 100,000 unique players are admitted, including paid and complimentary entries. "
        "Players can continue joining while it is running.\n\n"
        "🏅 FIXED CASH PRIZES\n" + prizes + "\n\n"
        "The final top five are determined from the official Mega Tournament leaderboard. "
        "Cash awards are subject to final score verification and are paid separately."
    )
    buttons = []
    has_access = mega_tournament.has_access(uid)
    if campaign.get("status") != "finished" and not has_access:
        buttons.append([InlineKeyboardButton("📱 PAY KSh 35 WITH M-PESA", callback_data="mega_mpesa")])
        buttons.append([InlineKeyboardButton("🌍 PAY US$0.80 WITH PAYPAL", callback_data="mega_paypal")])
        buttons.append([InlineKeyboardButton("⭐ PAY 50 TELEGRAM STARS", callback_data="mega_stars")])
    elif has_access:
        text += "\n\n✅ Your paid membership is confirmed."
        if campaign.get("status") == "active":
            buttons.append([InlineKeyboardButton(
                "🏁 PLAY 100K MEGA TOURNAMENT",
                web_app=WebAppInfo(url=game_url("mega"))
            )])
        elif campaign.get("status") == "waiting":
            text += "\nThe game will unlock automatically when the 100,000 paid-member target is reached."
    await query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons) if buttons else None)


async def send_paypal_checkout(update, context, payment_type):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    if reject_banned(uid):
        await query.message.reply_text("🚫 Your account has been suspended.")
        return
    try:
        result = await create_paypal_order(uid, payment_type)
        await query.message.reply_text(
            "🌍 PAYPAL SECURE CHECKOUT\\n\\n"
            f"Amount: US${result['amount']} {result['currency']}\\n"
            "Open the secure PayPal link below to complete payment. "
            "Tournament access activates only after PayPal confirms the captured payment.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔐 CONTINUE TO PAYPAL", url=result["approval_url"])
            ]]),
        )
    except ValueError as exc:
        await query.message.reply_text(str(exc))
    except Exception:
        logger.exception("PayPal order creation failed")
        await query.message.reply_text(
            "❌ PayPal checkout could not be started. No payment was taken and no access was granted. Please try again."
        )


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
    elif payment_type in ("tournament_10k", "tournament_1k"):
        cfg = capacity_tournaments.TOURNAMENTS[payment_type]
        campaign = capacity_tournaments.get_status(payment_type)
        if campaign.get("status") != "waiting":
            await query.message.reply_text("⛔ This tournament is closed.")
            return
        if capacity_tournaments.has_access(payment_type, uid):
            await query.message.reply_text("✅ Your tournament entry is already confirmed.")
            return
        context.user_data["awaiting_mpesa_phone"] = payment_type
        heading = "📱 " + cfg["name"].upper()
        price = "KSh 50"
    elif payment_type == "mega_tournament":
        campaign = mega_tournament.get_status()
        if campaign.get("status") == "finished":
            await query.message.reply_text("⛔ The 100K Mega Tournament has finished and is closed.")
            return
        if mega_tournament.has_access(uid):
            await query.message.reply_text("✅ Your Mega Tournament membership is already confirmed.")
            return
        context.user_data["awaiting_mpesa_phone"] = "mega_tournament"
        heading = "📱 100K MEGA TOURNAMENT ENTRY"
        price = "KSh 35"
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
            amount_text = "KSh 50" if payment_type in ("tournament_10k", "tournament_1k") else ("KSh 25" if payment_type == "weekly_entry" else ("KSh 35" if payment_type == "mega_tournament" else "KSh 200"))
            access_text = capacity_tournaments.TOURNAMENTS[payment_type]["name"] if payment_type in ("tournament_10k", "tournament_1k") else ("weekly tournament entry" if payment_type == "weekly_entry" else ("100K Mega Tournament entry" if payment_type == "mega_tournament" else "monthly membership"))
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

    elif query.data == "mega_info":
        await mega_tournament_information(update, context)

    elif query.data in ("capacity_info_tournament_10k", "capacity_info_tournament_1k"):
        await capacity_tournament_information(update, context, query.data.replace("capacity_info_", ""))

    elif query.data in ("capacity_mpesa_tournament_10k", "capacity_mpesa_tournament_1k"):
        await request_mpesa_phone(update, context, query.data.replace("capacity_mpesa_", ""))

    elif query.data in ("capacity_paypal_tournament_10k", "capacity_paypal_tournament_1k"):
        await send_paypal_checkout(update, context, query.data.replace("capacity_paypal_", ""))

    elif query.data in ("capacity_stars_tournament_10k", "capacity_stars_tournament_1k"):
        await send_capacity_stars_invoice(update, context, query.data.replace("capacity_stars_", ""))

    elif query.data in ("capacity_leaderboard_tournament_10k", "capacity_leaderboard_tournament_1k"):
        await capacity_leaderboard_message(update, context, query.data.replace("capacity_leaderboard_", ""))

    elif query.data == "mega_mpesa":
        await request_mpesa_phone(update, context, "mega_tournament")

    elif query.data == "mega_paypal":
        await send_paypal_checkout(update, context, "mega_tournament")

    elif query.data == "mega_stars":
        await send_mega_stars_invoice(update, context)

    elif query.data == "weekly_stars":
        await send_weekly_invoice(update, context)

    elif query.data == "weekly_paypal":
        await send_paypal_checkout(update, context, "weekly_entry")

    elif query.data == "weekly_mpesa":
        await request_mpesa_phone(update, context, "weekly_entry")

    elif query.data == "monthly_join":
        await monthly_join_options(update, context)

    elif query.data == "monthly_stars":
        await send_monthly_invoice(update, context)

    elif query.data == "monthly_paypal":
        await send_paypal_checkout(update, context, "monthly_membership")

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
# PLAYER NOTIFICATION PREFERENCES
# ============================================================

def notification_preferences_keyboard(preferences):
    labels = {
        "tournament_alerts": "🏆 Tournament reminders",
        "winner_alerts": "🥇 Winner announcements",
        "daily_reminders": "🎮 Daily free-game reminders",
        "announcements": "📢 News and promotions",
    }
    rows = []
    for key, label in labels.items():
        state = "ON ✅" if preferences.get(key) else "OFF 🔕"
        rows.append([
            InlineKeyboardButton(
                f"{label}: {state}",
                callback_data=f"alerts_toggle_{key}",
            )
        ])
    return InlineKeyboardMarkup(rows)


async def alerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update)
    uid = update.effective_user.id
    preferences = notification_service.get_preferences(uid)
    await update.effective_message.reply_text(
        "🔔 <b>NEON RUSH ARENA ALERT SETTINGS</b>\\n\\n"
        "Choose which messages you want to receive. You can change these settings at any time.\\n\\n"
        "Tournament and winner alerts are on by default. Daily reminders and promotional announcements are off until you enable them.",
        reply_markup=notification_preferences_keyboard(preferences),
        parse_mode="HTML",
    )


async def notification_preference_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    if query.data == "alerts_open":
        preferences = notification_service.get_preferences(uid)
        await query.message.reply_text(
            "🔔 <b>NEON RUSH ARENA ALERT SETTINGS</b>\\n\\nChoose which messages you want to receive.",
            reply_markup=notification_preferences_keyboard(preferences),
            parse_mode="HTML",
        )
        return
    key = str(query.data or "").replace("alerts_toggle_", "", 1)
    allowed = {"tournament_alerts", "winner_alerts", "daily_reminders", "announcements"}
    if key not in allowed:
        return
    current = notification_service.get_preferences(uid)
    updated = notification_service.set_preferences(
        uid,
        {key: not current.get(key, False)},
    )
    if updated is None:
        await query.answer("Please start the bot first.", show_alert=True)
        return
    await query.edit_message_reply_markup(
        reply_markup=notification_preferences_keyboard(updated)
    )


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

    if query.invoice_payload in ("tournament_10k_50_stars_v1", "tournament_1k_50_stars_v1"):
        kind = query.invoice_payload.replace("_50_stars_v1", "")
        campaign = capacity_tournaments.get_status(kind)
        if query.total_amount != 50 or campaign.get("status") != "waiting":
            await query.answer(ok=False, error_message="This tournament Stars invoice is invalid or closed.")
            return
        if capacity_tournaments.has_access(kind, uid):
            await query.answer(ok=False, error_message="Your tournament entry is already confirmed.")
            return
        await query.answer(ok=True)
        return

    if query.invoice_payload == "mega_tournament_50_stars_v1":
        campaign = mega_tournament.get_status()
        if query.total_amount != 50 or campaign.get("status") == "finished":
            await query.answer(ok=False, error_message="This Mega Tournament Stars invoice is invalid or closed.")
            return
        if mega_tournament.has_access(uid):
            await query.answer(ok=False, error_message="Your Mega Tournament entry is already confirmed.")
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

    if payment.invoice_payload in ("tournament_10k_50_stars_v1", "tournament_1k_50_stars_v1"):
        kind = payment.invoice_payload.replace("_50_stars_v1", "")
        if payment.total_amount != 50:
            await update.message.reply_text("❌ Invalid tournament Stars payment amount.")
            return
        try:
            result = capacity_tournaments.confirm_stars_payment(
                kind, uid, payment.telegram_payment_charge_id,
                payment.provider_payment_charge_id, payment.total_amount,
            )
            if not result.get("processed"):
                await update.message.reply_text("Payment received but could not be applied automatically. Please contact support and do not pay again.")
                return
            await update.message.reply_text(
                "🎉 PAYMENT SUCCESSFUL!\n\n"
                f"Your 50 Telegram Stars entry to the {capacity_tournaments.TOURNAMENTS[kind]['name']} is confirmed. "
                "You are eligible for its top-five prizes."
            )
        except Exception:
            logger.exception("Capacity tournament Stars payment processing failed")
            await update.message.reply_text("Payment received, but tournament activation needs support assistance.")
        return

    if payment.invoice_payload == "mega_tournament_50_stars_v1":
        if payment.total_amount != 50:
            await update.message.reply_text("❌ Invalid Mega Tournament Stars payment amount.")
            return
        try:
            result = mega_tournament.confirm_stars_payment(
                uid=uid,
                telegram_charge_id=payment.telegram_payment_charge_id,
                provider_charge_id=payment.provider_payment_charge_id,
                amount=payment.total_amount,
            )
            if not result.get("processed"):
                await update.message.reply_text("This Stars payment was received but could not be applied automatically. Please contact support and do not pay again.")
                return
            await update.message.reply_text(
                "🎉 PAYMENT SUCCESSFUL!\n\n"
                "Your 50 Telegram Stars entry to the 100K Mega Tournament is confirmed. "
                "You are eligible for the same top-five cash prizes as all other admitted players."
            )
        except Exception:
            logger.exception("Mega Tournament Stars payment processing failed")
            await update.message.reply_text("Payment received, but Mega Tournament activation needs support assistance.")
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
    application.add_handler(CommandHandler("alerts", alerts))
    application.add_handler(CommandHandler("admin", admin))
    application.add_handler(CommandHandler("grant", grant))
    application.add_handler(CommandHandler("revoke", revoke))
    application.add_handler(CommandHandler("ban", ban))
    application.add_handler(CommandHandler("unban", unban))

    application.add_handler(CallbackQueryHandler(notification_preference_callback, pattern=r"^alerts_(open|toggle_)"))
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

