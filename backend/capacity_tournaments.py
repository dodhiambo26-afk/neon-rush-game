"""Independent 10K and 1K tournament membership, capacity and prize services."""
from datetime import datetime, timedelta, timezone
from firebase_admin import firestore
import database as db

TOURNAMENTS = {
    "tournament_10k": {
        "slug": "10k", "name": "NEON RUSH 10,000-Player Tournament",
        "target": 10000, "entry_kes": 50, "entry_usd": "0.80", "entry_stars": 50,
        "session_key": "nra-10k-v1",
        "prizes": [
            {"position": 1, "amount_kes": 100000},
            {"position": 2, "amount_kes": 50000},
            {"position": 3, "amount_kes": 30000},
            {"position": 4, "amount_kes": 20000},
            {"position": 5, "amount_kes": 15000},
        ],
    },
    "tournament_1k": {
        "slug": "1k", "name": "NEON RUSH 1,000-Player Tournament",
        "target": 1000, "entry_kes": 50, "entry_usd": "0.80", "entry_stars": 50,
        "session_key": "nra-1k-v1",
        "prizes": [
            {"position": 1, "amount_kes": 10000},
            {"position": 2, "amount_kes": 5000},
            {"position": 3, "amount_kes": 3000},
            {"position": 4, "amount_kes": 2000},
            {"position": 5, "amount_kes": 1500},
        ],
    },
}

def _config(kind):
    try:
        return TOURNAMENTS[str(kind)]
    except KeyError:
        raise ValueError("Unknown tournament type.")

def _campaign(kind):
    return db.db().collection("capacity_tournaments").document(str(kind))

def _member_collection(kind):
    return db.db().collection(str(kind) + "_members")

def _member(kind, uid):
    return _member_collection(kind).document(str(int(uid)))

def get_status(kind):
    cfg = _config(kind)
    ref = _campaign(kind)
    snap = ref.get()
    if not snap.exists:
        ref.set({
            "name": cfg["name"], "target_members": cfg["target"],
            "entry_price_kes": cfg["entry_kes"], "confirmed_members": 0,
            "complimentary_members": 0, "total_members": 0, "status": "waiting",
            "duration_hours": 24, "created_at": datetime.now(timezone.utc),
            "prizes": cfg["prizes"],
        }, merge=True)
        snap = ref.get()
    data = snap.to_dict() or {}
    data.setdefault("target_members", cfg["target"])
    data.setdefault("confirmed_members", 0)
    if "complimentary_members" not in data:
        data["complimentary_members"] = sum(
            1 for row in _member_collection(kind).where("source", "==", "admin").stream()
        )
    data["total_members"] = int(data.get("confirmed_members", 0)) + int(data.get("complimentary_members", 0))
    data.setdefault("status", "waiting")
    data.setdefault("duration_hours", 24)
    data.setdefault("prizes", cfg["prizes"])
    if data.get("status") == "active":
        end = data.get("ends_at")
        if isinstance(end, datetime) and datetime.now(timezone.utc) >= end:
            ref.set({"status": "finished", "finished_at": datetime.now(timezone.utc)}, merge=True)
            data["status"] = "finished"
    ref.set({
        "name": cfg["name"], "target_members": cfg["target"],
        "entry_price_kes": cfg["entry_kes"], "prizes": cfg["prizes"],
        "complimentary_members": int(data.get("complimentary_members", 0)),
        "total_members": data["total_members"],
    }, merge=True)
    return data

def _apply_admission(txn, kind, cfg, uid, member_ref, campaign_ref, payment_fields, source):
    member = next(txn.get(member_ref), None)
    campaign = next(txn.get(campaign_ref), None)
    if member is not None and member.exists:
        return False, "already_member", "waiting"
    cd = campaign.to_dict() if campaign is not None and campaign.exists else {}
    paid = int(cd.get("confirmed_members", 0)) + (0 if source == "admin" else 1)
    free = int(cd.get("complimentary_members", 0)) + (1 if source == "admin" else 0)
    total = paid + free
    now = datetime.now(timezone.utc)
    fields = {"confirmed_members": paid, "complimentary_members": free,
              "total_members": total, "updated_at": now}
    status = cd.get("status", "waiting")
    if status == "waiting" and total >= int(cd.get("target_members", cfg["target"])):
        hours = max(1, min(720, int(cd.get("duration_hours", 24))))
        fields.update({"status": "active", "started_at": now,
                       "ends_at": now + timedelta(hours=hours),
                       "started_automatically": True})
        status = "active"
    txn.set(campaign_ref, fields, merge=True)
    txn.set(member_ref, {
        "telegram_id": str(uid), "source": source, "joined_at": now,
        "paid_at": now if source != "admin" else None, **payment_fields,
    })
    return True, None, status

def has_access(kind, uid):
    return _member(kind, uid).get().exists

def has_pending_payment(kind, uid):
    return any(
        str((row.to_dict() or {}).get("telegram_id")) == str(int(uid))
        and (row.to_dict() or {}).get("payment_type") == kind
        and (row.to_dict() or {}).get("status") in ("pending", "initiated")
        for row in db.db().collection("mpesa_transactions").stream()
    )

def grant_admin_access(kind, uid, admin_id):
    cfg = _config(kind)
    get_status(kind)
    member_ref, campaign_ref = _member(kind, uid), _campaign(kind)
    txn = db.db().transaction()
    @firestore.transactional
    def apply(t):
        ok, _, _ = _apply_admission(t, kind, cfg, str(int(uid)), member_ref, campaign_ref,
            {"admin_granted": True, "granted_by": str(admin_id), "amount_kes": 0, "payment_id": None}, "admin")
        return ok
    return apply(txn)

def revoke_access(kind, uid):
    cfg = _config(kind)
    get_status(kind)
    member_ref, campaign_ref = _member(kind, uid), _campaign(kind)
    txn = db.db().transaction()
    @firestore.transactional
    def apply(t):
        member = next(t.get(member_ref), None)
        campaign = next(t.get(campaign_ref), None)
        if member is None or not member.exists:
            return False
        md = member.to_dict() or {}
        cd = campaign.to_dict() if campaign is not None and campaign.exists else {}
        paid = int(cd.get("confirmed_members", 0))
        free = int(cd.get("complimentary_members", 0))
        if md.get("source") == "admin":
            free = max(0, free - 1)
        else:
            paid = max(0, paid - 1)
        t.set(campaign_ref, {"confirmed_members": paid, "complimentary_members": free,
            "total_members": paid + free, "updated_at": datetime.now(timezone.utc)}, merge=True)
        t.delete(member_ref)
        return True
    return apply(txn)

def list_members(kind, limit=2000):
    _config(kind)
    rows = _member_collection(kind).limit(max(1, min(2000, int(limit)))).stream()
    return [{"id": row.id, **(row.to_dict() or {})} for row in rows]

def leaderboard(kind, limit=100):
    cfg = _config(kind)
    rows = []
    # Keep the query to one indexed equality filter; sort locally so this
    # feature does not depend on a new Firestore composite index.
    for snap in db.db().collection("scores").where("mode", "==", str(kind)).stream():
        item = snap.to_dict() or {}
        if str(item.get("session_key")) != cfg["session_key"]:
            continue
        if db.is_banned(item.get("telegram_id")):
            continue
        rows.append(item)
    rows.sort(key=lambda item: int(item.get("score", 0)), reverse=True)
    return rows[:max(1, min(1000, int(limit)))]

def confirm_stars_payment(kind, uid, telegram_charge_id, provider_charge_id, amount):
    cfg = _config(kind)
    if int(amount) != cfg["entry_stars"] or not telegram_charge_id:
        return {"processed": False, "reason": "invalid_payment"}
    uid = str(int(uid))
    member_ref, campaign_ref = _member(kind, uid), _campaign(kind)
    payref = db.db().collection("payments").document("stars_" + str(telegram_charge_id))
    txn = db.db().transaction()
    @firestore.transactional
    def apply(t):
        prior = next(t.get(payref), None)
        if prior is not None and prior.exists:
            return {"processed": False, "reason": "duplicate_payment"}
        ok, reason, status = _apply_admission(t, kind, cfg, uid, member_ref, campaign_ref, {
            "payment_id": "stars_" + str(telegram_charge_id),
            "telegram_payment_charge_id": str(telegram_charge_id),
            "provider_payment_charge_id": str(provider_charge_id or ""),
            "amount": cfg["entry_stars"], "currency": "XTR",
        }, "telegram_stars")
        if not ok:
            return {"processed": False, "reason": reason}
        now = datetime.now(timezone.utc)
        t.set(payref, {"payment_id": "stars_" + str(telegram_charge_id),
            "telegram_payment_charge_id": str(telegram_charge_id),
            "provider_payment_charge_id": str(provider_charge_id or ""),
            "telegram_id": int(uid), "amount": cfg["entry_stars"], "currency": "XTR",
            "payment_type": kind, "provider": "telegram_stars", "status": "success",
            "created_at": now, "reconciliation_status": "confirmed_success", "unique_member": True})
        return {"processed": True, "success": True, "telegram_id": uid, "tournament_status": status}
    return apply(txn)

def confirm_mpesa_payment(kind, internal_id, payment_id, receipt, checkout_id, source):
    cfg = _config(kind)
    pending_ref = db.db().collection("mpesa_transactions").document(str(internal_id))
    payref = db.db().collection("payments").document(str(payment_id))
    pending = pending_ref.get()
    if not pending.exists:
        return {"processed": False, "reason": "unknown_payment"}
    pdata = pending.to_dict() or {}
    uid = str(pdata.get("telegram_id") or "")
    if not uid or pdata.get("payment_type") != kind:
        return {"processed": False, "reason": "invalid_payment"}
    member_ref, campaign_ref = _member(kind, uid), _campaign(kind)
    txn = db.db().transaction()
    @firestore.transactional
    def apply(t):
        current = next(t.get(pending_ref), None)
        prior = next(t.get(payref), None)
        if current is None or not current.exists:
            return {"processed": False, "reason": "unknown_payment"}
        cd = current.to_dict() or {}
        if cd.get("status") not in ("pending", "initiated"):
            return {"processed": False, "reason": "payment_not_pending"}
        if int(cd.get("amount", 0)) != cfg["entry_kes"]:
            t.update(pending_ref, {"status": "review_required", "review_reason": "Tournament payment amount mismatch."})
            return {"processed": False, "reason": "amount_mismatch"}
        if prior is not None and prior.exists:
            return {"processed": False, "reason": "duplicate_receipt"}
        ok, reason, status = _apply_admission(t, kind, cfg, uid, member_ref, campaign_ref, {
            "payment_id": payment_id, "mpesa_receipt": receipt,
            "amount_kes": cfg["entry_kes"], "checkout_request_id": checkout_id,
        }, "mpesa")
        if not ok:
            return {"processed": False, "reason": reason}
        now = datetime.now(timezone.utc)
        t.set(payref, {"payment_id": payment_id, "telegram_id": uid,
            "amount": cfg["entry_kes"], "currency": "KES", "payment_type": kind,
            "provider": "mpesa", "mpesa_receipt": receipt,
            "checkout_request_id": checkout_id, "created_at": now,
            "reconciled_by": source, "receipt_pending": False, "unique_member": True})
        t.update(pending_ref, {"status": "success", "payment_id": payment_id,
            "mpesa_receipt": receipt, "paid_at": now, "updated_at": now,
            "reconciliation_status": "confirmed_success", "reconciliation_source": source})
        return {"processed": True, "success": True, "telegram_id": uid,
                "payment_type": kind, "payment_id": payment_id, "tournament_status": status}
    return apply(txn)

def confirm_paypal_payment(kind, uid, capture_id, order_id, amount):
    cfg = _config(kind)
    if str(amount) != cfg["entry_usd"]:
        return {"processed": False, "reason": "amount_mismatch"}
    uid = str(int(uid))
    member_ref, campaign_ref = _member(kind, uid), _campaign(kind)
    payref = db.db().collection("payments").document("paypal_" + str(capture_id))
    txn = db.db().transaction()
    @firestore.transactional
    def apply(t):
        prior = next(t.get(payref), None)
        if prior is not None and prior.exists:
            return {"processed": False, "reason": "duplicate_payment"}
        ok, reason, status = _apply_admission(t, kind, cfg, uid, member_ref, campaign_ref, {
            "payment_id": "paypal_" + str(capture_id), "amount": str(amount),
            "currency": "USD", "paypal_order_id": order_id,
        }, "paypal")
        if not ok:
            return {"processed": False, "reason": reason}
        now = datetime.now(timezone.utc)
        t.set(payref, {"payment_id": "paypal_" + str(capture_id),
            "paypal_order_id": order_id, "paypal_capture_id": capture_id,
            "telegram_id": int(uid), "amount": str(amount), "currency": "USD",
            "payment_type": kind, "provider": "paypal", "created_at": now,
            "reconciliation_status": "confirmed_success", "unique_member": True})
        return {"processed": True, "success": True, "telegram_id": uid, "tournament_status": status}
    return apply(txn)
