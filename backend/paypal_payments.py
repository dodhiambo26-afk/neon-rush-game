"""PayPal Orders v2 integration. Access is granted only after server-confirmed capture."""
import os, uuid
from datetime import datetime, timedelta, timezone
import httpx
from firebase_admin import firestore
import database as db
import mega_tournament
import capacity_tournaments

PRICES = {"weekly_entry": "0.80", "monthly_membership": "2.00", "mega_tournament": "0.80", "tournament_10k": "0.80", "tournament_1k": "0.80"}

def base_url():
    return "https://api-m.paypal.com" if os.getenv("PAYPAL_MODE", "live").lower() == "live" else "https://api-m.sandbox.paypal.com"

async def access_token(client):
    cid, secret = os.getenv("PAYPAL_CLIENT_ID", "").strip(), os.getenv("PAYPAL_CLIENT_SECRET", "").strip()
    if not cid or not secret:
        raise RuntimeError("PayPal credentials are missing from the backend environment.")
    r = await client.post(base_url()+"/v1/oauth2/token", auth=(cid, secret), data={"grant_type":"client_credentials"})
    r.raise_for_status()
    return r.json()["access_token"]

def period_key(kind):
    if kind == "weekly_entry": return db.current_weekly_session()
    if kind == "monthly_membership": return db.current_month_key()
    if kind in ("tournament_10k", "tournament_1k"):
        return capacity_tournaments.TOURNAMENTS[kind]["session_key"]
    return mega_tournament.SESSION_KEY

async def create_paypal_order(uid, kind):
    if kind not in PRICES: raise ValueError("Unsupported PayPal payment.")
    uid = int(uid)
    if db.is_banned(uid): raise ValueError("Your account is suspended.")
    key = period_key(kind)
    if kind == "weekly_entry" and db.has_weekly_entry(uid,key): raise ValueError("You already have weekly access.")
    if kind == "monthly_membership" and db.has_monthly_access(uid,key): raise ValueError("Your monthly membership is active.")
    if kind in ("tournament_10k", "tournament_1k"):
        campaign = capacity_tournaments.get_status(kind)
        if campaign.get("status") != "waiting": raise ValueError("This tournament is full or closed; no new payments are being accepted.")
        if capacity_tournaments.has_access(kind, uid): raise ValueError("Your tournament entry is already confirmed.")
        capacity_tournaments.reserve_slot(kind, uid, "paypal", ttl_minutes=30)
    if kind == "mega_tournament":
        campaign = mega_tournament.get_status()
        if campaign.get("status") == "finished": raise ValueError("The Mega Tournament is closed.")
        if mega_tournament.has_access(uid): raise ValueError("Your Mega membership is already confirmed.")
    amount, internal = PRICES[kind], uuid.uuid4().hex
    backend = os.getenv("BACKEND_PUBLIC_URL","https://neon-rush-game-backend.onrender.com").rstrip("/")
    payload = {"intent":"CAPTURE","purchase_units":[{"reference_id":kind,"custom_id":internal,"invoice_id":"NRA-"+internal,"description":"NEON RUSH ARENA "+kind.replace("_"," "),"amount":{"currency_code":"USD","value":amount}}],"payment_source":{"paypal":{"experience_context":{"brand_name":"NEON RUSH ARENA","shipping_preference":"NO_SHIPPING","user_action":"PAY_NOW","return_url":backend+"/api/paypal/return","cancel_url":"https://t.me/NeonRushGamingBot"}}}}
    try:
        async with httpx.AsyncClient(timeout=25) as client:
            token=await access_token(client)
            r=await client.post(base_url()+"/v2/checkout/orders",json=payload,headers={"Authorization":"Bearer "+token})
            r.raise_for_status()
            order=r.json()
        approval=next((x["href"] for x in order.get("links",[]) if x.get("rel") in ("payer-action","approve")),None)
        if not approval: raise RuntimeError("PayPal did not return a checkout link.")
    except Exception:
        if kind in ("tournament_10k", "tournament_1k"):
            capacity_tournaments.release_slot(kind, uid, "paypal_order_creation_failed")
        raise
    db.db().collection("paypal_orders").document(order["id"]).set({"order_id":order["id"],"internal_id":internal,"telegram_id":str(uid),"payment_type":kind,"period_key":key,"amount":amount,"currency":"USD","status":"created","created_at":datetime.now(timezone.utc)})
    return {"approval_url":approval,"amount":amount,"currency":"USD"}

async def capture_and_grant(order_id):
    order_id=str(order_id or "").strip()
    ref=db.db().collection("paypal_orders").document(order_id)
    snap=ref.get()
    if not snap.exists: raise ValueError("This PayPal order is not recognized.")
    p=snap.to_dict() or {}
    if p.get("status")=="completed": return {"success":True,"already_processed":True}
    async with httpx.AsyncClient(timeout=25) as client:
        token=await access_token(client)
        r=await client.post(base_url()+f"/v2/checkout/orders/{order_id}/capture",json={},headers={"Authorization":"Bearer "+token})
        if r.status_code in (200,201): data=r.json()
        else:
            check=await client.get(base_url()+f"/v2/checkout/orders/{order_id}",headers={"Authorization":"Bearer "+token})
            check.raise_for_status(); data=check.json()
    unit=(data.get("purchase_units") or [{}])[0]
    cap=((unit.get("payments") or {}).get("captures") or [{}])[0]
    amt=cap.get("amount") or {}
    if data.get("status")!="COMPLETED" or cap.get("status")!="COMPLETED": raise ValueError("PayPal has not confirmed payment capture.")
    if amt.get("currency_code")!=p.get("currency") or amt.get("value")!=p.get("amount") or str(unit.get("custom_id"))!=str(p.get("internal_id")): raise ValueError("PayPal payment details do not match the server order.")
    capture_id=str(cap.get("id") or "")
    if not capture_id: raise ValueError("PayPal capture reference is missing.")
    uid=int(p["telegram_id"]); kind=p["payment_type"]; key=str(p["period_key"]); now=datetime.now(timezone.utc)
    if kind in ("tournament_10k", "tournament_1k"):
        result = capacity_tournaments.confirm_paypal_payment(kind, uid, capture_id, order_id, p["amount"])
        if not result.get("processed"):
            # Capture succeeded but admission failed (for example, the final
            # slot was taken concurrently). Keep the capture auditable and
            # prevent another capture attempt; support must resolve/refund it.
            ref.update({
                "status": "review_required",
                "capture_id": capture_id,
                "captured_at": now,
                "review_reason": "PayPal capture succeeded but tournament admission was not granted: " + str(result.get("reason", "unknown")),
                "reconciliation_status": "review_required",
            })
            db.db().collection("payments").document("paypal_" + capture_id).set({
                "payment_id": "paypal_" + capture_id,
                "paypal_order_id": order_id,
                "paypal_capture_id": capture_id,
                "telegram_id": uid,
                "amount": p["amount"],
                "currency": "USD",
                "payment_type": kind,
                "provider": "paypal",
                "status": "review_required",
                "review_reason": "Captured payment without tournament admission; manual resolution required.",
                "created_at": now,
                "reconciliation_status": "review_required",
            }, merge=True)
            raise ValueError("PayPal received your payment but could not grant tournament entry. Your payment is flagged for admin review; do not pay again. Contact support with your PayPal transaction ID.")
        ref.update({"status": "completed", "capture_id": capture_id, "completed_at": now})
        return {"success": True, "capture_id": capture_id, "payment_type": kind}
    payref=db.db().collection("payments").document("paypal_"+capture_id)
    tx=db.db().transaction()
    @firestore.transactional
    def apply(t):
        current=next(t.get(ref),None); prior=next(t.get(payref),None)
        if current is None or not current.exists: return False
        if (current.to_dict() or {}).get("status")=="completed": return True
        if (current.to_dict() or {}).get("status")!="created" or (prior is not None and prior.exists): return False
        if kind=="weekly_entry":
            aid=db._weekly_entry_id(uid,key); ar=db.db().collection("weekly_entries").document(aid); old=next(t.get(ar),None)
            if old is None or not old.exists: t.set(ar,{"entry_id":aid,"telegram_id":uid,"session_key":key,"granted_at":now,"source":"paypal","payment_id":"paypal_"+capture_id},merge=True)
        elif kind=="monthly_membership":
            aid=db._monthly_membership_id(uid,key); ar=db.db().collection("monthly_memberships").document(aid); old=next(t.get(ar),None)
            if old is None or not old.exists: t.set(ar,{"membership_id":aid,"telegram_id":uid,"month_key":key,"granted_at":now,"expires_at":db.month_end_datetime(key),"source":"paypal","payment_id":"paypal_"+capture_id},merge=True)
            t.set(db.db().collection("users").document(str(uid)),{"telegram_id":uid,"premium_until":db.month_end_datetime(key),"updated_at":now},merge=True)
        elif kind=="mega_tournament":
            mr=db.db().collection("mega_tournament_members").document(str(uid)); cr=db.db().collection("mega_tournaments").document("current")
            member=next(t.get(mr),None); campaign=next(t.get(cr),None)
            if member is None or not member.exists:
                cd=campaign.to_dict() if campaign is not None and campaign.exists else {}
                count=int(cd.get("confirmed_members",0))+1; complimentary=int(cd.get("complimentary_members",0)); total=count+complimentary; fields={"confirmed_members":count,"total_members":total,"updated_at":now}
                if cd.get("status","waiting")=="waiting" and total>=int(cd.get("target_members",mega_tournament.TARGET)):
                    hours=max(1,min(720,int(cd.get("duration_hours",24)))); fields.update({"status":"active","started_at":now,"ends_at":now+timedelta(hours=hours),"started_automatically":True})
                t.set(cr,fields,merge=True); t.set(mr,{"telegram_id":str(uid),"payment_id":"paypal_"+capture_id,"amount":p["amount"],"currency":"USD","source":"paypal","paid_at":now,"joined_at":now})
        t.set(payref,{"payment_id":"paypal_"+capture_id,"paypal_order_id":order_id,"paypal_capture_id":capture_id,"telegram_id":uid,"amount":p["amount"],"currency":"USD","payment_type":kind,"period_key":key,"provider":"paypal","created_at":now})
        t.update(ref,{"status":"completed","capture_id":capture_id,"completed_at":now})
        return True
    if not apply(tx): raise ValueError("Payment could not be safely applied. Contact support with your PayPal transaction ID.")
    return {"success":True,"capture_id":capture_id,"payment_type":kind}

async def verify_webhook(headers, event):
    webhook_id = os.getenv("PAYPAL_WEBHOOK_ID", "").strip()
    if not webhook_id:
        raise RuntimeError("PAYPAL_WEBHOOK_ID is not configured.")
    required = {
        "auth_algo": headers.get("paypal-auth-algo"),
        "cert_url": headers.get("paypal-cert-url"),
        "transmission_id": headers.get("paypal-transmission-id"),
        "transmission_sig": headers.get("paypal-transmission-sig"),
        "transmission_time": headers.get("paypal-transmission-time"),
    }
    if not all(required.values()):
        raise ValueError("PayPal webhook signature headers are incomplete.")
    body = {
        **required,
        "webhook_id": webhook_id,
        "webhook_event": event,
    }
    async with httpx.AsyncClient(timeout=25) as client:
        token = await access_token(client)
        response = await client.post(
            base_url() + "/v1/notifications/verify-webhook-signature",
            json=body,
            headers={"Authorization": "Bearer " + token},
        )
        response.raise_for_status()
        result = response.json()
    return result.get("verification_status") == "SUCCESS"
