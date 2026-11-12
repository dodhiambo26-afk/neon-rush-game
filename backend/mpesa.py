import base64
import hmac
import logging
import os
import re
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
from fastapi import APIRouter, HTTPException, Request

import database as db

logger = logging.getLogger(__name__)
router = APIRouter()
KENYA = ZoneInfo("Africa/Nairobi")
MONTHLY_MEMBERSHIP_KES = 200


def normalize_kenyan_phone(value):
    digits = re.sub(r"\D", "", str(value or ""))
    if digits.startswith("0") and len(digits) == 10:
        digits = "254" + digits[1:]
    elif digits.startswith("7") or digits.startswith("1"):
        if len(digits) == 9:
            digits = "254" + digits
    if not re.fullmatch(r"254[17]\d{8}", digits):
        raise ValueError("Enter a valid Kenyan mobile number, e.g. 0712345678.")
    return digits


async def initiate_stk_push(telegram_id, phone, payment_type="monthly_membership"):
    key = os.getenv("MPESA_CONSUMER_KEY", "").strip()
    secret = os.getenv("MPESA_CONSUMER_SECRET", "").strip()
    passkey = os.getenv("MPESA_PASSKEY", "").strip()
    shortcode = os.getenv("MPESA_SHORTCODE", "").strip()
    environment = os.getenv("MPESA_ENVIRONMENT", "sandbox").strip().lower()
    callback_secret = os.getenv("MPESA_CALLBACK_SECRET", "").strip()
    render_url = os.getenv("RENDER_EXTERNAL_URL", "").rstrip("/")

    if not all([key, secret, passkey, shortcode, callback_secret, render_url]):
        raise RuntimeError(
            "M-Pesa is not fully configured. The administrator must set "
            "MPESA_CALLBACK_SECRET and confirm the Daraja Sandbox credentials in Render."
        )
    if environment not in ("sandbox", "production"):
        raise RuntimeError("MPESA_ENVIRONMENT must be sandbox or production.")

    phone = normalize_kenyan_phone(phone)
    if payment_type == "weekly_entry":
        period_key = db.current_weekly_session()
        if db.has_weekly_entry(telegram_id, period_key):
            return {"already_active": True}
        amount = 25
        reference = "NRWEEKLY"
        description = "Weekly Tournament Entry"
    elif payment_type == "monthly_membership":
        period_key = db.current_month_key()
        if db.has_monthly_access(telegram_id, period_key):
            return {"already_active": True}
        amount = MONTHLY_MEMBERSHIP_KES
        reference = "NEONRUSH"
        description = "Monthly Membership"
    else:
        raise ValueError("Unsupported M-Pesa payment type.")

    base_url = (
        "https://sandbox.safaricom.co.ke"
        if environment == "sandbox"
        else "https://api.safaricom.co.ke"
    )
    callback_url = f"{render_url}/api/mpesa/callback/{callback_secret}"
    internal_id = uuid.uuid4().hex
    db.create_mpesa_pending(internal_id, telegram_id, phone, period_key, payment_type)

    now = datetime.now(KENYA).strftime("%Y%m%d%H%M%S")
    password = base64.b64encode(
        f"{shortcode}{passkey}{now}".encode("utf-8")
    ).decode("utf-8")
    transaction_type = os.getenv(
        "MPESA_TRANSACTION_TYPE", "CustomerPayBillOnline"
    ).strip()

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            token_response = await client.get(
                f"{base_url}/oauth/v1/generate",
                params={"grant_type": "client_credentials"},
                auth=(key, secret),
            )
            token_response.raise_for_status()
            access_token = token_response.json().get("access_token")
            if not access_token:
                raise RuntimeError("Daraja did not return an access token.")

            payload = {
                "BusinessShortCode": shortcode,
                "Password": password,
                "Timestamp": now,
                "TransactionType": transaction_type,
                "Amount": amount,
                "PartyA": phone,
                "PartyB": shortcode,
                "PhoneNumber": phone,
                "CallBackURL": callback_url,
                "AccountReference": reference,
                "TransactionDesc": description,
            }
            response = await client.post(
                f"{base_url}/mpesa/stkpush/v1/processrequest",
                json=payload,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            response.raise_for_status()
            data = response.json()
    except Exception as exc:
        db.fail_mpesa_pending(internal_id, type(exc).__name__)
        raise RuntimeError(
            "Safaricom could not be reached or rejected the request. Please try again."
        ) from exc

    if str(data.get("ResponseCode", "")) != "0":
        db.fail_mpesa_pending(internal_id, data.get("ResponseDescription") or data)
        raise RuntimeError(
            str(data.get("ResponseDescription") or "Safaricom rejected the STK request.")
        )

    checkout_id = data.get("CheckoutRequestID")
    merchant_id = data.get("MerchantRequestID")
    if not checkout_id or not merchant_id:
        db.fail_mpesa_pending(internal_id, "Missing Daraja request identifiers.")
        raise RuntimeError("Daraja response did not include payment request IDs.")

    db.update_mpesa_checkout(internal_id, checkout_id, merchant_id, {
        "response_code": data.get("ResponseCode"),
        "customer_message": data.get("CustomerMessage"),
    })
    return {
        "already_active": False,
        "customer_message": data.get("CustomerMessage")
            or "Check your phone and enter your M-Pesa PIN to complete payment.",
    }


@router.post("/api/mpesa/callback/{secret}")
async def mpesa_callback(secret: str, request: Request):
    expected = os.getenv("MPESA_CALLBACK_SECRET", "").strip()
    if not expected or not hmac.compare_digest(secret, expected):
        raise HTTPException(status_code=404, detail="Not found.")

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid callback JSON.")

    result = db.process_mpesa_callback(payload)
    if result.get("success") and result.get("telegram_id"):
        application = getattr(request.app.state, "bot_application", None)
        if application:
            try:
                await application.bot.send_message(
                    chat_id=int(result["telegram_id"]),
                    text=(
                        "🎉 M-PESA PAYMENT CONFIRMED!\n\n"
                        + (
                            "Your KSh 25 weekly tournament entry is now confirmed "
                            "for this week's session.\n\n"
                            if result.get("payment_type") == "weekly_entry"
                            else "Your KSh 200 NEON RUSH ARENA monthly membership "
                            "is now active until the end of this month.\n\n"
                        )
                        + f"Receipt: {result.get('receipt', 'confirmed')}\n"
                        + (
                            "Good luck in the weekly tournament!"
                            if result.get("payment_type") == "weekly_entry"
                            else "Good luck in the monthly tournament!"
                        )
                    ),
                )
            except Exception:
                logger.exception("Could not send M-Pesa confirmation to player.")

    return {"ResultCode": 0, "ResultDesc": "Accepted"}
