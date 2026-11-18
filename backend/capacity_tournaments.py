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
    expire_stale_reservations(kind)
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
    # Only write static configuration here. Membership counters are owned by
    # atomic admission/revocation transactions; writing snapshot-derived totals
    # from this read path could overwrite a concurrent payment transaction.
    ref.set({
        "name": cfg["name"], "target_members": cfg["target"],
        "entry_price_kes": cfg["entry_kes"], "prizes": cfg["prizes"],
    }, merge=True)
    data.setdefault("reserved_count", 0)
    return data

def _reservation(kind, uid):
    return db.db().collection(str(kind) + "_reservations").document(str(int(uid)))


def release_slot(kind, uid, reason="released", expected_checkout_id=None):
    """Idempotently release an unconsumed capacity hold."""
    _config(kind)
    reservation_ref, campaign_ref = _reservation(kind, uid), _campaign(kind)
    txn = db.db().transaction()

    @firestore.transactional
    def apply(t):
        reservation = next(t.get(reservation_ref), None)
        campaign = next(t.get(campaign_ref), None)
        if reservation is None or not reservation.exists:
            return False
        rd = reservation.to_dict() or {}
        if rd.get("status") != "reserved":
            return False
        if expected_checkout_id is not None and str(rd.get("checkout_id") or "") != str(expected_checkout_id):
            return False
        if reason == "expired":
            expiry = rd.get("expires_at")
            if not isinstance(expiry, datetime) or expiry > datetime.now(timezone.utc):
                return False
        cd = campaign.to_dict() if campaign is not None and campaign.exists else {}
        t.update(campaign_ref, {
            "reserved_count": max(0, int(cd.get("reserved_count", 0)) - 1),
            "updated_at": datetime.now(timezone.utc),
        })
        t.update(reservation_ref, {
            "status": reason, "released_at": datetime.now(timezone.utc),
        })
        return True
    return apply(txn)


def reservation_is_valid(kind, uid, expected_checkout_id=None):
    _config(kind)
    snap = _reservation(kind, uid).get()
    if not snap.exists:
        return False
    data = snap.to_dict() or {}
    expiry = data.get("expires_at")
    return (
        data.get("status") == "reserved"
        and isinstance(expiry, datetime)
        and expiry > datetime.now(timezone.utc)
        and (
            expected_checkout_id is None
            or str(data.get("checkout_id") or "") == str(expected_checkout_id)
        )
    )


def refresh_reservation(kind, uid, expected_checkout_id, ttl_minutes=10):
    """Extend a live Stars reservation after Telegram's pre-checkout approval."""
    _config(kind)
    ref = _reservation(kind, uid)
    now = datetime.now(timezone.utc)
    expires = now + timedelta(minutes=max(5, min(20, int(ttl_minutes))))
    txn = db.db().transaction()

    @firestore.transactional
    def apply(t):
        snap = next(t.get(ref), None)
        if snap is None or not snap.exists:
            return False
        data = snap.to_dict() or {}
        current_expiry = data.get("expires_at")
        if (
            data.get("status") != "reserved"
            or data.get("provider") != "telegram_stars"
            or str(data.get("checkout_id") or "") != str(expected_checkout_id)
            or not isinstance(current_expiry, datetime)
            or current_expiry <= now
        ):
            return False
        t.update(ref, {"expires_at": expires, "precheckout_at": now})
        return True
    return apply(txn)


def reserve_slot(kind, uid, provider, checkout_id=None, ttl_minutes=30):
    """Atomically hold one place before creating a provider checkout."""
    cfg = _config(kind)
    expire_stale_reservations(kind)
    get_status(kind)
    uid = str(int(uid))
    reservation_ref, campaign_ref = _reservation(kind, uid)
    now = datetime.now(timezone.utc)
    expires = now + timedelta(minutes=max(5, min(60, int(ttl_minutes))))
    txn = db.db().transaction()

    @firestore.transactional
    def apply(t):
        reservation = next(t.get(reservation_ref), None)
        campaign = next(t.get(campaign_ref), None)
        rd = reservation.to_dict() if reservation is not None and reservation.exists else {}
        cd = campaign.to_dict() if campaign is not None and campaign.exists else {}
        if rd.get("status") == "reserved":
            old_expiry = rd.get("expires_at")
            if rd.get("provider") == "mpesa":
                return False, "Your M-Pesa request is still awaiting a final Safaricom result. Do not start another payment; contact support if it remains pending."
            if isinstance(old_expiry, datetime) and old_expiry > now:
                return False, "You already have a payment checkout in progress. Complete it or wait for it to expire."
            # Expired PayPal/Stars hold is reclaimed in the same atomic campaign update.
            reserved = max(0, int(cd.get("reserved_count", 0)) - 1)
        else:
            reserved = int(cd.get("reserved_count", 0))
        if cd.get("status", "waiting") != "waiting":
            return False, "This tournament is full or closed."
        admitted = int(cd.get("confirmed_members", 0)) + int(cd.get("complimentary_members", 0))
        if admitted + reserved >= int(cd.get("target_members", cfg["target"])):
            return False, "All tournament places are currently taken or reserved."
        t.set(campaign_ref, {"reserved_count": reserved + 1, "updated_at": now}, merge=True)
        t.set(reservation_ref, {
            "telegram_id": uid, "provider": str(provider),
            "checkout_id": str(checkout_id or ""),
            "status": "reserved", "created_at": now, "expires_at": expires,
        }, merge=True)
        return True, None
    ok, reason = apply(txn)
    if not ok:
        raise ValueError(reason)
    return {"reserved": True, "expires_at": expires}


def expire_stale_reservations(kind, limit=500):
    """Reclaim timed-out holds; safe to call periodically or before checkout."""
    _config(kind)
    now = datetime.now(timezone.utc)
    refs = db.db().collection(str(kind) + "_reservations").where("expires_at", "<=", now).limit(max(1, min(2000, int(limit)))).stream()
    released = 0
    for snap in refs:
        data = snap.to_dict() or {}
        expiry = data.get("expires_at")
        # Safaricom STK requests cannot be cancelled reliably from this
        # service. Keep their slots held until Daraja returns a terminal
        # success/failure; otherwise a late customer PIN could charge after
        # the slot had been offered to someone else.
        if data.get("provider") == "mpesa":
            continue
        if isinstance(expiry, datetime) and expiry <= now:
            if release_slot(kind, snap.id, "expired"):
                released += 1
    return released


def _apply_admission(txn, kind, cfg, uid, member_ref, campaign_ref, payment_fields, source):
    member = next(txn.get(member_ref), None)
    campaign = next(txn.get(campaign_ref), None)
    reservation_ref = _reservation(kind, uid)
    reservation = next(txn.get(reservation_ref), None)
    if member is not None and member.exists:
        return False, "already_member", "waiting"
    cd = campaign.to_dict() if campaign is not None and campaign.exists else {}
    rd = reservation.to_dict() if reservation is not None and reservation.exists else {}
    now = datetime.now(timezone.utc)
    expiry = rd.get("expires_at")
    expected_checkout_id = payment_fields.get("reservation_checkout_id")
    reservation_expired = (
        not isinstance(expiry, datetime) or expiry <= now
    )
    if rd.get("provider") == "mpesa":
        reservation_expired = False
    if source != "admin" and (
        rd.get("status") != "reserved"
        or reservation_expired
        or (
            expected_checkout_id is not None
            and str(rd.get("checkout_id") or "") != str(expected_checkout_id)
        )
    ):
        return False, "reservation_missing_or_expired", cd.get("status", "waiting")
    reserved_count = int(cd.get("reserved_count", 0))
    if source != "admin":
        reserved_count = max(0, reserved_count - 1)
    paid = int(cd.get("confirmed_members", 0)) + (0 if source == "admin" else 1)
    free = int(cd.get("complimentary_members", 0)) + (1 if source == "admin" else 0)
    total = paid + free
    now = datetime.now(timezone.utc)
    fields = {"confirmed_members": paid, "complimentary_members": free,
              "total_members": total, "reserved_count": reserved_count, "updated_at": now}
    status = cd.get("status", "waiting")
    target = int(cd.get("target_members", cfg["target"]))
    # These are fixed-capacity tournaments: do not admit a 10,001st or
    # 1,001st player, or accept late callbacks after the tournament closes.
    if status != "waiting":
        return False, "tournament_closed", status
    # Admin complimentary grants must not consume places already held
    # by players who are in a live provider checkout.
    if total + reserved_count > target:
        return False, "capacity_reached_reserved_slots", status
    if total == target:
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
    if source != "admin":
        txn.update(reservation_ref, {"status": "consumed", "consumed_at": now})
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

def list_members(kind, limit=10000):
    _config(kind)
    rows = _member_collection(kind).limit(max(1, min(10000, int(limit)))).stream()
    return [{"id": row.id, **(row.to_dict() or {})} for row in rows]

def leaderboard(kind, limit=100):
    cfg = _config(kind)
    best_by_player = {}
    # Only scores replayed and verified by the server can determine cash prizes.
    # Keep a single equality query to avoid a composite Firestore index.
    for snap in db.db().collection("scores").where("mode", "==", str(kind)).stream():
        item = snap.to_dict() or {}
        uid = str(item.get("telegram_id") or "").strip()
        if (
            not uid
            or str(item.get("session_key")) != cfg["session_key"]
            or item.get("verification_status") != "server_replayed"
            or db.is_banned(uid)
        ):
            continue
        try:
            score = int(item.get("score", 0))
        except (ValueError, TypeError):
            continue
        recorded = item.get("created_at")
        if isinstance(recorded, datetime):
            if recorded.tzinfo is None:
                recorded = recorded.replace(tzinfo=timezone.utc)
            timestamp = recorded.timestamp()
        else:
            timestamp = float("inf")
        existing = best_by_player.get(uid)
        if existing is None or score > existing["_rank_score"] or (
            score == existing["_rank_score"] and timestamp < existing["_rank_time"]
        ):
            item["_rank_score"] = score
            item["_rank_time"] = timestamp
            best_by_player[uid] = item
    rows = list(best_by_player.values())
    rows.sort(key=lambda item: (
        -int(item.get("_rank_score", 0)),
        item.get("_rank_time", float("inf")),
        str(item.get("telegram_id", "")),
    ))
    for item in rows:
        item.pop("_rank_score", None)
        item.pop("_rank_time", None)
    return rows[:max(1, min(1000, int(limit)))]

def confirm_stars_payment(kind, uid, telegram_charge_id, provider_charge_id, amount, reservation_id=None):
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
            "reservation_checkout_id": str(reservation_id) if reservation_id is not None else None,
        }, "telegram_stars")
        if not ok:
            now = datetime.now(timezone.utc)
            t.set(payref, {
                "payment_id": "stars_" + str(telegram_charge_id),
                "telegram_payment_charge_id": str(telegram_charge_id),
                "provider_payment_charge_id": str(provider_charge_id or ""),
                "telegram_id": int(uid), "amount": cfg["entry_stars"],
                "currency": "XTR", "payment_type": kind,
                "provider": "telegram_stars", "status": "review_required",
                "review_reason": "Stars payment was confirmed but admission was not granted: " + str(reason),
                "created_at": now, "reconciliation_status": "review_required",
            })
            return {"processed": False, "reason": reason, "review_required": True}
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
            # A late successful Daraja callback/query can arrive after a
            # timeout was marked failed. Preserve the confirmed receipt for
            # reconciliation instead of silently losing evidence of payment.
            if cd.get("status") in ("failed", "review_required"):
                now = datetime.now(timezone.utc)
                t.set(payref, {
                    "payment_id": payment_id, "telegram_id": uid,
                    "amount": cfg["entry_kes"], "currency": "KES",
                    "payment_type": kind, "provider": "mpesa",
                    "mpesa_receipt": receipt, "checkout_request_id": checkout_id,
                    "status": "review_required",
                    "review_reason": "Late M-Pesa success arrived after the pending transaction had reached a terminal state.",
                    "created_at": now, "reconciliation_status": "review_required",
                }, merge=True)
                t.update(pending_ref, {
                    "status": "review_required", "payment_id": payment_id,
                    "mpesa_receipt": receipt, "paid_at": now,
                    "reconciliation_status": "review_required",
                    "review_reason": "Late M-Pesa success after terminal pending status.",
                })
                return {"processed": False, "reason": "late_success_review_required", "review_required": True}
            return {"processed": False, "reason": "payment_not_pending"}
        if int(cd.get("amount", 0)) != cfg["entry_kes"]:
            t.update(pending_ref, {"status": "review_required", "review_reason": "Tournament payment amount mismatch."})
            return {"processed": False, "reason": "amount_mismatch"}
        if prior is not None and prior.exists:
            return {"processed": False, "reason": "duplicate_receipt"}
        ok, reason, status = _apply_admission(t, kind, cfg, uid, member_ref, campaign_ref, {
            "payment_id": payment_id, "mpesa_receipt": receipt,
            "amount_kes": cfg["entry_kes"], "checkout_request_id": checkout_id,
            "reservation_checkout_id": str(internal_id),
        }, "mpesa")
        if not ok:
            # Provider-confirmed money must always leave an auditable payment
            # record, even when admission failed due to a duplicate member,
            # closed capacity, or a duplicate receipt.
            t.update(pending_ref, {
                "status": "review_required",
                "reconciliation_status": "review_required",
                "review_reason": "Payment confirmed but tournament admission was not granted: " + str(reason),
                "payment_id": payment_id,
                "mpesa_receipt": receipt,
                "paid_at": datetime.now(timezone.utc),
                "reconciliation_source": source,
            })
            t.set(payref, {
                "payment_id": payment_id, "telegram_id": uid,
                "amount": cfg["entry_kes"], "currency": "KES",
                "payment_type": kind, "provider": "mpesa",
                "mpesa_receipt": receipt, "checkout_request_id": checkout_id,
                "status": "review_required",
                "review_reason": "Payment confirmed but tournament admission was not granted: " + str(reason),
                "created_at": datetime.now(timezone.utc),
                "reconciliation_status": "review_required",
            })
            return {"processed": False, "reason": reason, "review_required": True}
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

def confirm_paypal_payment(kind, uid, capture_id, order_id, amount, internal_id=None):
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
            "reservation_checkout_id": str(internal_id) if internal_id is not None else None,
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
