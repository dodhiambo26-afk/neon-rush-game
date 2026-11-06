
import os
import asyncio
from functools import wraps
from datetime import datetime, timezone

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    WebAppInfo, LabeledPrice
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    PreCheckoutQueryHandler, MessageHandler, filters,
)

import database as db

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
WEBAPP_URL = os.getenv("WEBAPP_URL", "")
ADMIN_IDS = {
    int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
}
PREMIUM_PRICE = int(os.getenv("PREMIUM_PRICE", "100"))
PREMIUM_DAYS = int(os.getenv("PREMIUM_DAYS", "30"))

application = None


def premium_active(uid):
    user = db.get_user(uid)
    if not user or not user["premium_until"]:
        return False
    try:
        expiry = datetime.fromisoformat(user["premium_until"])
        return expiry > datetime.now(timezone.utc)
    except ValueError:
        return False


def admin_only(func):
    @wraps(func)
    async def wrapper(update, context):
        if update.effective_user.id not in ADMIN_IDS:
            await update.effective_message.reply_text("Access denied.")
            return
        return await func(update, context)
    return wrapper


def main_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏎️ Play Free Game", web_app=WebAppInfo(url=WEBAPP_URL))],
        [InlineKeyboardButton("🏆 Premium Tournament", callback_data="tournament")],
        [InlineKeyboardButton("⭐ Get Premium", callback_data="premium")],
        [InlineKeyboardButton("👤 My Profile", callback_data="profile")],
        [InlineKeyboardButton("🥇 Leaderboard", callback_data="leaderboard")],
    ])


async def start(update: Update, context):
    user = update.effective_user
    db.upsert_user(
        user.id, user.username, user.first_name, user.last_name
    )
    await update.message.reply_text(
        f"Welcome to NEON RUSH, {user.first_name}!\n\n"
        "Race, compete and climb the leaderboard.",
        reply_markup=main_keyboard()
    )


async def menu(update: Update, context):
    await update.callback_query.answer()
    await update.callback_query.message.reply_text(
        "NEON RUSH Menu", reply_markup=main_keyboard()
    )


async def buttons(update: Update, context):
    query = update.callback_query
    await query.answer()
    user = query.from_user
    uid = user.id

    db.upsert_user(uid, user.username, user.first_name, user.last_name)

    if db.is_banned(uid):
        await query.message.reply_text("Your account is restricted.")
        return

    if query.data == "premium":
        price = db.get_premium_price(PREMIUM_PRICE)
        await query.message.reply_text(
            f"Premium membership costs {price} Telegram Stars "
            f"for {PREMIUM_DAYS} days."
        )
        await context.bot.send_invoice(
            chat_id=uid,
            title="NEON RUSH Premium",
            description="30 days of premium tournament access.",
            payload=f"premium:{uid}:{PREMIUM_DAYS}",
            provider_token="",
            currency="XTR",
            prices=[LabeledPrice("Premium membership", price)],
        )

    elif query.data == "tournament":
        if not premium_active(uid):
            await query.message.reply_text(
                "🏆 Premium membership is required for the tournament.\n"
                "Tap ⭐ Get Premium to subscribe.",
                reply_markup=main_keyboard()
            )
        else:
            if not WEBAPP_URL:
                await query.message.reply_text("Game is temporarily unavailable.")
                return
            await query.message.reply_text(
                "Welcome to the premium tournament!",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        "🏁 Enter Tournament",
                        web_app=WebAppInfo(url=WEBAPP_URL)
                    )
                ]])
            )

    elif query.data == "profile":
        status = "Active ⭐" if premium_active(uid) else "Free player"
        await query.message.reply_text(
            f"👤 Profile\nID: {uid}\nMembership: {status}"
        )

    elif query.data == "leaderboard":
        rows = db.list_top_scores(10)
        if not rows:
            await query.message.reply_text("No tournament scores yet.")
            return
        text = "🏆 DAILY LEADERBOARD\n\n"
        for i, row in enumerate(rows, 1):
            text += f"{i}. {row['display_name']} — {row['score']}\n"
        await query.message.reply_text(text)


async def precheckout(update: Update, context):
    query = update.pre_checkout_query
    parts = query.invoice_payload.split(":")
    valid = (
        len(parts) == 3
        and parts[0] == "premium"
        and parts[1].isdigit()
        and int(parts[1]) == query.from_user.id
        and query.currency == "XTR"
        and query.total_amount == db.get_premium_price(PREMIUM_PRICE)
        and not db.is_banned(query.from_user.id)
    )
    await query.answer(
        ok=valid,
        error_message=None if valid else "Payment could not be verified."
    )


async def successful_payment(update: Update, context):
    user = update.effective_user
    payment = update.message.successful_payment
    parts = payment.invoice_payload.split(":")

    if (
        len(parts) != 3
        or parts[0] != "premium"
        or not parts[1].isdigit()
        or not parts[2].isdigit()
        or int(parts[1]) != user.id
        or payment.currency != "XTR"
        or payment.total_amount != db.get_premium_price(PREMIUM_PRICE)
    ):
        await update.message.reply_text("Payment details could not be verified. Contact support.")
        return

    db.upsert_user(user.id, user.username, user.first_name, user.last_name)

    inserted = db.add_payment(
        user.id,
        payment.telegram_payment_charge_id,
        payment.provider_payment_charge_id,
        payment.total_amount,
        payment.currency,
    )

    if inserted:
        db.set_premium(user.id, int(parts[2]))
        await update.message.reply_text(
            "✅ Payment successful! Your premium membership is active.",
            reply_markup=main_keyboard()
        )
    else:
        await update.message.reply_text("This payment was already processed.")


async def myid(update: Update, context):
    await update.message.reply_text(
        f"Your Telegram ID: {update.effective_user.id}"
    )


@admin_only
async def admin(update: Update, context):
    stats = db.get_stats()
    await update.message.reply_text(
        "🛠 NEON RUSH ADMIN\n"
        f"Players: {stats['players']}\n"
        f"Premium members: {stats['premium']}\n"
        f"Payments: {stats['payments']}\n"
        f"Stars received: {stats['stars']}\n\n"
        "/grant USER_ID DAYS\n"
        "/revoke USER_ID\n"
        "/ban USER_ID\n"
        "/unban USER_ID\n"
        "/setprice STARS"
    )


@admin_only
async def grant(update: Update, context):
    try:
        uid, days = map(int, context.args)
        if days < 1 or days > 3650:
            raise ValueError()
        if not db.get_user(uid):
            await update.message.reply_text("Player not found.")
            return
        db.set_premium(uid, days)
        await update.message.reply_text("Premium membership granted.")
    except (ValueError, TypeError):
        await update.message.reply_text("Usage: /grant USER_ID DAYS")


@admin_only
async def revoke(update: Update, context):
    try:
        uid = int(context.args[0])
        db.revoke_premium(uid)
        await update.message.reply_text("Premium membership revoked.")
    except (ValueError, IndexError):
        await update.message.reply_text("Usage: /revoke USER_ID")


@admin_only
async def ban(update: Update, context):
    try:
        uid = int(context.args[0])
        db.set_banned(uid, True)
        await update.message.reply_text("Player banned.")
    except (ValueError, IndexError):
        await update.message.reply_text("Usage: /ban USER_ID")


@admin_only
async def unban(update: Update, context):
    try:
        uid = int(context.args[0])
        db.set_banned(uid, False)
        await update.message.reply_text("Player unbanned.")
    except (ValueError, IndexError):
        await update.message.reply_text("Usage: /unban USER_ID")


@admin_only
async def setprice(update: Update, context):
    try:
        price = int(context.args[0])
        if price < 1 or price > 100000:
            raise ValueError()
        db.set_premium_price(price)
        await update.message.reply_text(f"Premium price set to {price} Stars.")
    except (ValueError, IndexError):
        await update.message.reply_text("Usage: /setprice STARS")


async def paysupport(update: Update, context):
    await update.message.reply_text(
        "For payment assistance, contact the game administrator."
    )


async def post_init(app):
    db.init_db()


def build_application():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN environment variable is missing.")
    if not WEBAPP_URL.startswith("https://"):
        raise RuntimeError("WEBAPP_URL must be a valid HTTPS URL.")

    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", menu))
    app.add_handler(CommandHandler("myid", myid))
    app.add_handler(CommandHandler("admin", admin))
    app.add_handler(CommandHandler("grant", grant))
    app.add_handler(CommandHandler("revoke", revoke))
    app.add_handler(CommandHandler("ban", ban))
    app.add_handler(CommandHandler("unban", unban))
    app.add_handler(CommandHandler("setprice", setprice))
    app.add_handler(CommandHandler("paysupport", paysupport))
    app.add_handler(CallbackQueryHandler(buttons))
    app.add_handler(PreCheckoutQueryHandler(precheckout))
    app.add_handler(
        MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment)
    )
    return app


async def run_bot():
    app = build_application()
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()
