"""Safaricom Daraja B2C prize payouts.

Payouts are deliberately admin-triggered per award. A Firestore transaction
claims each award before the network call, preventing duplicate submissions.
Ambiguous provider/network outcomes remain under review and are never retried
automatically.
"""
import hmac
import logging
import os
import uuid
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Header, HTTPException, Request

import database as db
from mpesa import normalize_kenyan_phone

logger = logging.getLogger(__name__)
router = APIRouter()


def _base_url():
    env = os.getenv("MPESA_ENVIRONMENT", "sandbox").strip().lower()
    if env not in ("sandbox", "production"):
        raise HTTPException(503, "MPESA_ENVIRONMENT must be sandbox or production.")
    return "https://api.safaricom.co.ke" if env == "production" else "https://sandbox.safaricom.co.ke"


def _required(name):
    value = os.getenv(name, "").strip()
    if not value:
        raise HTTPException(503, f"Missing Render environment variable: {name}")
    return value


@router.post("/api/admin/prizes/{award_id}/send-mpesa")
async def send_prize_mpesa(
    award_id: str,
    x_telegram_init_data: str = Header(default="", alias="X-Telegram-Init-Data"),
):
    from server import require_admin, record_admin_action
    admin = require_admin(x_telegram_init_data)

    award_ref = db.db().collection("prize_awards").document(str(award_id))
    award_snap = award_ref.get()
    if not award_snap.exists:
        raise HTTPException(404, "Prize award not found.")
    award = award_snap.to_dict() or {}

    if award.get("status") != "pending":
        raise HTTPException(409, f"Prize is not payable; current status: {award.get('status')}.")
    if str(award.get("currency", "KES")).upper() != "KES" or int(award.get("amount", 0)) < 1:
        raise HTTPException(400, "Prize must be a positive KES amount.")
    if str(award.get("period_type")) == "weekly" and str(award.get("period_key")) == db.current_weekly_session():
        raise HTTPException(409, "The current weekly tournament cannot be paid before it closes.")
    if str(award.get("period_type")) == "monthly" and str(award.get("period_key")) == db.current_month_key():
        raise HTTPException(409, "The current monthly tournament cannot be paid before it closes.")

    uid = str(award.get("telegram_id", ""))
    player = db.get_user(uid) or {}
    if not player.get("mpesa_phone_verified") or not player.get("mpesa_payout_phone"):
        raise HTTPException(409, "Player has no verified M-Pesa payout number. The player must complete a successful M-Pesa payment first.")
    try:
        phone = normalize_kenyan_phone(player["mpesa_payout_phone"])
    except ValueError:
        raise HTTPException(409, "The player's verified M-Pesa number is invalid.")

    key = _required("MPESA_CONSUMER_KEY")
    secret = _required("MPESA_CONSUMER_SECRET")
    initiator = _required("MPESA_B2C_INITIATOR_NAME")
    security_credential = _required("MPESA_B2C_SECURITY_CREDENTIAL")
    shortcode = _required("MPESA_B2C_SHORTCODE")
    callback_secret = _required("MPESA_B2C_CALLBACK_SECRET")
    render_url = _required("RENDER_EXTERNAL_URL").rstrip("/")
    result_url = f"{render_url}/api/mpesa/b2c/result/{callback_secret}"
    timeout_url = f"{render_url}/api/mpesa/b2c/timeout/{callback_secret}"

    # Atomically reserve the award before contacting Daraja. Never reset an
    # ambiguous reservation to pending: it requires reconciliation/manual review.
    payout_id = uuid.uuid4().hex
    transaction = db.db().transaction()

    from firebase_admin import firestore

    @firestore.transactional
    def reserve(tx):
        snap = tx.get(award_ref)
        if not snap.exists:
            return False
        current = snap.to_dict() or {}
        if current.get("status") != "pending":
            return False
        tx.update(award_ref, {
            "status": "processing",
            "payout_id": payout_id,
            "payout_phone": phone,
            "payout_started_at": datetime.now(timezone.utc),
            "payout_started_by": str(admin["id"]),
        })
        return True

    if not reserve(transaction):
        raise HTTPException(409, "This prize has already been claimed for payout.")

    base = _base_url()
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            token_response = await client.get(
                f"{base}/oauth/v1/generate",
                params={"grant_type": "client_credentials"},
                auth=(key, secret),
            )
            token_response.raise_for_status()
            token = token_response.json().get("access_token")
            if not token:
                raise RuntimeError("Daraja did not return an access token.")
            payload = {
                "InitiatorName": initiator,
                "SecurityCredential": security_credential,
                "CommandID": "BusinessPayment",
                "Amount": int(award["amount"]),
                "PartyA": shortcode,
                "PartyB": phone,
                "Remarks": f"NEON RUSH prize position {award.get('position')}",
                "QueueTimeOutURL": timeout_url,
                "ResultURL": result_url,
                "Occasion": str(award_id)[:64],
            }
            response = await client.post(
                f"{base}/mpesa/b2c/v1/paymentrequest",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
            data = response.json()
    except Exception as exc:
        logger.exception("B2C request outcome is uncertain for award %s", award_id)
        award_ref.set({
            "status": "review_required",
            "payout_error": type(exc).__name__,
            "payout_error_at": datetime.now(timezone.utc),
        }, merge=True)
        raise HTTPException(502, "Safaricom response could not be confirmed. Prize moved to review; do not retry until checked in Daraja.") from exc

    if str(data.get("ResponseCode", "")) != "0":
        award_ref.set({
            "status": "failed",
            "payout_response": data,
            "payout_response_at": datetime.now(timezone.utc),
        }, merge=True)
        raise HTTPException(502, str(data.get("ResponseDescription") or "Daraja rejected the B2C request."))

    award_ref.set({
        "b2c_conversation_id": data.get("ConversationID"),
        "b2c_originator_conversation_id": data.get("OriginatorConversationID"),
        "payout_response": data,
        "payout_submitted_at": datetime.now(timezone.utc),
    }, merge=True)
    record_admin_action(admin["id"], "B2C_PRIZE_SUBMITTED", int(uid), f"{award_id}: {award['amount']} KES to {phone}")
    return {
        "success": True,
        "status": "processing",
        "message": "Safaricom accepted the payout request. This is not yet proof of payment; wait for the result callback.",
        "conversation_id": data.get("ConversationID"),
    }


async def _b2c_callback(secret: str, request: Request, expected_name: str):
    expected = os.getenv("MPESA_B2C_CALLBACK_SECRET", "").strip()
    if not expected or not hmac.compare_digest(secret, expected):
        raise HTTPException(404, "Not found.")
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(400, "Invalid callback JSON.")
    result = payload.get("Result") if isinstance(payload, dict) else None
    if not isinstance(result, dict):
        return {"ResultCode": 0, "ResultDesc": "Accepted"}
    conversation = str(result.get("ConversationID") or "")
    originator = str(result.get("OriginatorConversationID") or "")
    if not conversation and not originator:
        return {"ResultCode": 0, "ResultDesc": "Accepted"}
    matches = list(db.db().collection("prize_awards").where(
        "b2c_conversation_id", "==", conversation
    ).limit(1).stream()) if conversation else []
    if not matches and originator:
        matches = list(db.db().collection("prize_awards").where(
            "b2c_originator_conversation_id", "==", originator
        ).limit(1).stream())
    if not matches:
        logger.warning("Unmatched B2C callback: %s / %s", conversation, originator)
        return {"ResultCode": 0, "ResultDesc": "Accepted"}
    ref = matches[0].reference
    code = int(result.get("ResultCode", -1))
    params = result.get("ResultParameters", {}).get("ResultParameter", [])
    details = {
        str(item.get("Key")): item.get("Value")
        for item in params if isinstance(item, dict) and item.get("Key")
    } if isinstance(params, list) else {}
    status = "paid" if code == 0 else "failed"
    ref.set({
        "status": status,
        "b2c_result_code": code,
        "b2c_result_description": str(result.get("ResultDesc", ""))[:500],
        "b2c_receipt": str(details.get("TransactionReceipt") or ""),
        "b2c_transaction_amount": details.get("TransactionAmount"),
        "b2c_recipient": details.get("ReceiverPartyPublicName"),
        "b2c_completed_at": datetime.now(timezone.utc),
        "b2c_result_payload": result,
    }, merge=True)
    return {"ResultCode": 0, "ResultDesc": "Accepted"}


@router.post("/api/mpesa/b2c/result/{secret}")
async def b2c_result(secret: str, request: Request):
    return await _b2c_callback(secret, request, "result")


@router.post("/api/mpesa/b2c/timeout/{secret}")
async def b2c_timeout(secret: str, request: Request):
    # A timeout is ambiguous, not a failed payment. Never permit an automatic retry.
    expected = os.getenv("MPESA_B2C_CALLBACK_SECRET", "").strip()
    if not expected or not hmac.compare_digest(secret, expected):
        raise HTTPException(404, "Not found.")
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(400, "Invalid callback JSON.")
    result = payload.get("Result", {}) if isinstance(payload, dict) else {}
    conversation = str(result.get("ConversationID") or "")
    matches = list(db.db().collection("prize_awards").where(
        "b2c_conversation_id", "==", conversation
    ).limit(1).stream()) if conversation else []
    for snap in matches:
        snap.reference.set({
            "status": "review_required",
            "b2c_timeout_payload": result,
            "b2c_timeout_at": datetime.now(timezone.utc),
        }, merge=True)
    return {"ResultCode": 0, "ResultDesc": "Accepted"}
