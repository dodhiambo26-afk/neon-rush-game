"""100K Mega Tournament membership, launch timer and prize configuration."""
from datetime import datetime, timedelta, timezone
from firebase_admin import firestore
import database as db

TARGET = 100000
ENTRY_KES = 35
SESSION_KEY = "mega-100k-v1"
PRIZES = [
    {"position": 1, "amount_kes": 750000},
    {"position": 2, "amount_kes": 250000},
    {"position": 3, "amount_kes": 200000},
    {"position": 4, "amount_kes": 150000},
    {"position": 5, "amount_kes": 100000},
]

def _campaign():
    return db.db().collection("mega_tournaments").document("current")

def _member(uid):
    return db.db().collection("mega_tournament_members").document(str(int(uid)))

def get_status():
    ref = _campaign()
    snap = ref.get()
    if not snap.exists:
        ref.set({
            "name": "NEON RUSH 100K Mega Tournament",
            "target_members": TARGET,
            "entry_price_kes": ENTRY_KES,
            "confirmed_members": 0,
            "status": "waiting",
            "duration_hours": 24,
            "created_at": datetime.now(timezone.utc),
            "prizes": PRIZES,
        }, merge=True)
        snap = ref.get()
    data = snap.to_dict() or {}
    data.setdefault("target_members", TARGET)
    data.setdefault("entry_price_kes", ENTRY_KES)
    data.setdefault("confirmed_members", 0)
    data.setdefault("status", "waiting")
    data.setdefault("duration_hours", 24)
    data.setdefault("prizes", PRIZES)
    if data.get("status") == "active":
        end = data.get("ends_at")
        if isinstance(end, datetime) and datetime.now(timezone.utc) >= end:
            ref.set({"status": "finished", "finished_at": datetime.now(timezone.utc)}, merge=True)
            data["status"] = "finished"
    return data

def confirm_mpesa_payment(internal_id, payment_id, receipt, checkout_id, source):
    """Atomically mark payment successful and count one unique paid Telegram ID."""
    uid = None
    pending_ref = db.db().collection("mpesa_transactions").document(str(internal_id))
    payment_ref = db.db().collection("payments").document(str(payment_id))
    pending_snap = pending_ref.get()
    if not pending_snap.exists:
        return {"processed": False, "reason": "unknown_payment"}
    pending = pending_snap.to_dict() or {}
    uid = str(pending.get("telegram_id") or "")
    if not uid or pending.get("payment_type") != "mega_tournament":
        return {"processed": False, "reason": "invalid_mega_payment"}
    member_ref = _member(uid)
    campaign_ref = _campaign()
    transaction = db.db().transaction()

    @firestore.transactional
    def apply(txn):
        # Firestore Python Transaction.get(document_ref) yields a generator
        # in current client versions; consume each single-document result.
        current = next(txn.get(pending_ref), None)
        existing_payment = next(txn.get(payment_ref), None)
        member = next(txn.get(member_ref), None)
        campaign = next(txn.get(campaign_ref), None)
        if current is None or not current.exists:
            return {"processed": False, "reason": "unknown_payment"}
        current_data = current.to_dict() or {}
        if current_data.get("status") not in ("pending", "initiated"):
            return {"processed": False, "reason": "payment_not_pending"}
        paid_amount = int(current_data.get("amount", 0))
        if paid_amount not in (30, ENTRY_KES):
            txn.update(pending_ref, {"status": "review_required", "review_reason": "Mega tournament payment amount mismatch."})
            return {"processed": False, "reason": "amount_mismatch"}
        if existing_payment is not None and existing_payment.exists:
            txn.update(pending_ref, {"status": "review_required", "review_reason": "M-Pesa receipt already linked."})
            return {"processed": False, "reason": "duplicate_receipt"}
        campaign_data = campaign.to_dict() if campaign is not None and campaign.exists else {
            "name": "NEON RUSH 100K Mega Tournament",
            "target_members": TARGET,
            "entry_price_kes": ENTRY_KES,
            "confirmed_members": 0,
            "status": "waiting",
            "duration_hours": 24,
            "prizes": PRIZES,
        }
        now = datetime.now(timezone.utc)
        already_member = member is not None and member.exists
        count = int(campaign_data.get("confirmed_members", 0))
        if not already_member:
            count += 1
            txn.set(member_ref, {
                "telegram_id": uid,
                "payment_id": payment_id,
                "mpesa_receipt": receipt,
                "amount_kes": paid_amount,
                "source": "mpesa",
                "paid_at": now,
                "joined_at": now,
            })
        status = campaign_data.get("status", "waiting")
        fields = {"confirmed_members": count, "updated_at": now}
        if status == "waiting" and count >= int(campaign_data.get("target_members", TARGET)):
            duration = max(1, min(720, int(campaign_data.get("duration_hours", 24))))
            fields.update({
                "status": "active",
                "started_at": now,
                "ends_at": now + timedelta(hours=duration),
                "started_automatically": True,
            })
            status = "active"
        txn.set(campaign_ref, fields, merge=True)
        txn.set(payment_ref, {
            "payment_id": payment_id,
            "telegram_id": uid,
            "amount": paid_amount,
            "currency": "KES",
            "payment_type": "mega_tournament",
            "provider": "mpesa",
            "mpesa_receipt": receipt,
            "checkout_request_id": checkout_id,
            "created_at": now,
            "reconciled_by": source,
            "receipt_pending": False,
            "unique_member": not already_member,
        })
        txn.update(pending_ref, {
            "status": "success",
            "payment_id": payment_id,
            "mpesa_receipt": receipt,
            "paid_at": now,
            "updated_at": now,
            "reconciliation_status": "confirmed_success",
            "reconciliation_source": source,
        })
        return {
            "processed": True, "success": True, "telegram_id": uid,
            "payment_type": "mega_tournament", "payment_id": payment_id,
            "unique_member": not already_member, "tournament_status": status,
        }
    return apply(transaction)

def has_access(uid):
    return _member(uid).get().exists


def has_pending_payment(uid):
    """Prevent two simultaneous STK prompts for one Mega membership."""
    rows = db.db().collection("mpesa_transactions").stream()
    return any(
        str((row.to_dict() or {}).get("telegram_id")) == str(int(uid))
        and (row.to_dict() or {}).get("payment_type") == "mega_tournament"
        and (row.to_dict() or {}).get("status") in ("pending", "initiated")
        for row in rows
    )

def set_duration(hours, admin_id):
    hours = int(hours)
    if hours < 1 or hours > 720:
        raise ValueError("Duration must be between 1 and 720 hours.")
    ref = _campaign()
    snap = ref.get()
    data = snap.to_dict() if snap.exists else {}
    if data.get("status", "waiting") != "waiting":
        raise ValueError("Initial duration can only be changed before the tournament starts.")
    ref.set({"duration_hours": hours, "duration_set_by": str(admin_id), "duration_updated_at": datetime.now(timezone.utc)}, merge=True)
    return get_status()

def extend_time(hours, admin_id):
    hours = int(hours)
    if hours < 1 or hours > 720:
        raise ValueError("Extension must be between 1 and 720 hours.")
    ref = _campaign()
    transaction = db.db().transaction()
    @firestore.transactional
    def apply(txn):
        snap = next(txn.get(ref), None)
        if snap is None or not snap.exists:
            raise ValueError("Tournament has not started.")
        data = snap.to_dict() or {}
        if data.get("status") != "active" or not isinstance(data.get("ends_at"), datetime):
            raise ValueError("Only an active tournament can be extended.")
        now = datetime.now(timezone.utc)
        end = max(data["ends_at"], now) + timedelta(hours=hours)
        txn.update(ref, {"ends_at": end, "last_extension_hours": hours, "extended_by": str(admin_id), "extended_at": now})
        return end
    end = apply(transaction)
    return {"ends_at": end, "extension_hours": hours}

def list_members(limit=500):
    rows = _member_collection().order_by("paid_at", direction="ASCENDING").limit(max(1, min(2000, int(limit)))).stream()
    return [{"id": row.id, **(row.to_dict() or {})} for row in rows]

def _member_collection():
    return db.db().collection("mega_tournament_members")

def leaderboard(limit=10):
    rows = db.db().collection("scores").stream()
    best = {}
    for snap in rows:
        item = snap.to_dict() or {}
        if item.get("mode") != "mega" or str(item.get("session_key")) != SESSION_KEY:
            continue
        uid = str(item.get("telegram_id") or "")
        if not uid:
            continue
        old = best.get(uid)
        score = int(item.get("score", 0))
        if old is None or score > int(old.get("score", 0)):
            best[uid] = item
    ranked = sorted(best.values(), key=lambda x: (-int(x.get("score", 0)), str(x.get("created_at", "")), str(x.get("telegram_id", ""))))
    return ranked[:max(1, min(100, int(limit)))]
