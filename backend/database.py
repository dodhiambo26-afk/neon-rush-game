import os
import threading
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import firebase_admin
from firebase_admin import credentials, firestore


# ============================================================
# CONFIGURATION
# ============================================================

KENYA = ZoneInfo("Africa/Nairobi")
MONTHLY_STARS = 300
MONTHLY_PRIZE_PERCENT = 30
MONTHLY_RETAINED_PERCENT = 70

_lock = threading.RLock()
_db = None


# ============================================================
# FIRESTORE INITIALIZATION
# ============================================================

def init_db():
    global _db

    with _lock:
        if _db is not None:
            return _db

        if not firebase_admin._apps:
            service_account_path = os.getenv(
                "GOOGLE_APPLICATION_CREDENTIALS",
                "/etc/secrets/firebase-service-account.json"
            )

            if os.path.exists(service_account_path):
                cred = credentials.Certificate(service_account_path)
                firebase_admin.initialize_app(cred)
            else:
                raise RuntimeError(
                    "Firebase service account file not found. "
                    "Set GOOGLE_APPLICATION_CREDENTIALS or add the Render "
                    "Secret File at /etc/secrets/firebase-service-account.json"
                )

        _db = firestore.client()
        return _db


def db():
    return init_db()


def connect():
    """Compatibility function for older code."""
    return db()


def now_utc():
    return datetime.now(timezone.utc)


def now_kenya():
    return datetime.now(KENYA)


def timestamp():
    return now_utc()


def _doc(collection, doc_id):
    return db().collection(collection).document(str(doc_id))


def _get(collection, doc_id):
    snap = _doc(collection, doc_id).get()
    return snap.to_dict() if snap.exists else None


def _all(collection):
    return [s.to_dict() for s in db().collection(collection).stream()]


def _as_int(value, default=0):
    try:
        return int(value or 0)
    except (ValueError, TypeError):
        return default


def _user_id(uid):
    return str(uid)


def _period_key(value=None):
    if value:
        return str(value)
    return now_kenya().strftime("%Y-%m")


def current_month_key():
    return now_kenya().strftime("%Y-%m")


def current_weekly_session():
    """
    Weekly tournament session: Sunday 6 PM Kenya time to the
    following Sunday 6 PM Kenya time.
    """
    local = now_kenya()
    days_since_sunday = (local.weekday() + 1) % 7
    sunday = (local - timedelta(days=days_since_sunday)).replace(
        hour=18, minute=0, second=0, microsecond=0
    )

    if local < sunday:
        sunday -= timedelta(days=7)

    return sunday.strftime("%Y-%m-%dT%H:%M")


def weekly_session():
    return current_weekly_session()


def monthly_session():
    return current_month_key()


def month_end_datetime(month_key=None):
    key = _period_key(month_key)
    year, month = map(int, key.split("-"))

    if month == 12:
        next_month = datetime(year + 1, 1, 1, tzinfo=KENYA)
    else:
        next_month = datetime(year, month + 1, 1, tzinfo=KENYA)

    return next_month - timedelta(microseconds=1)


# ============================================================
# SETTINGS
# ============================================================

DEFAULT_SETTINGS = {
    "support_email": "neonrusharena@gmail.com",
    "support_whatsapp": "+254183375330",
    "weekly_locked": False,
    "monthly_price_stars": MONTHLY_STARS,
}


def get_setting(key, default=None):
    value = _get("settings", key)
    if value is None:
        return DEFAULT_SETTINGS.get(key, default)
    return value.get("value", default)


def set_setting(key, value, admin_id=None):
    _doc("settings", key).set({
        "key": key,
        "value": value,
        "updated_at": timestamp(),
        "updated_by": str(admin_id) if admin_id is not None else None,
    })
    return True


def list_settings():
    result = dict(DEFAULT_SETTINGS)
    for item in _all("settings"):
        result[item["key"]] = item.get("value")
    return result


def is_weekly_locked():
    return bool(get_setting("weekly_locked", False))


def set_weekly_locked(locked, admin_id=None):
    return set_setting("weekly_locked", bool(locked), admin_id)


# ============================================================
# USERS
# ============================================================

def upsert_user(uid, username=None, first_name=None, last_name=None):
    uid = _user_id(uid)
    ref = _doc("users", uid)
    old = ref.get().to_dict() or {}

    data = {
        "telegram_id": uid,
        "username": username if username is not None else old.get("username"),
        "first_name": first_name if first_name is not None else old.get("first_name"),
        "last_name": last_name if last_name is not None else old.get("last_name"),
        "banned": bool(old.get("banned", False)),
        "created_at": old.get("created_at", timestamp()),
        "updated_at": timestamp(),
    }

    ref.set(data, merge=True)
    return data


def get_user(uid):
    return _get("users", _user_id(uid))


def is_banned(uid):
    user = get_user(uid)
    return bool(user and user.get("banned", False))


def set_banned(uid, banned, admin_id=None):
    uid = _user_id(uid)
    user = get_user(uid) or {"telegram_id": uid}

    _doc("users", uid).set({
        **user,
        "telegram_id": uid,
        "banned": bool(banned),
        "updated_at": timestamp(),
        "moderated_by": str(admin_id) if admin_id is not None else None,
    }, merge=True)

    record_admin_activity(
        admin_id,
        "ban" if banned else "unban",
        uid,
        {"banned": bool(banned)}
    )
    return True


def search_players(query=""):
    query = str(query or "").strip().lower()
    players = _all("users")

    if not query:
        return players

    return [
        p for p in players
        if query in str(p.get("telegram_id", "")).lower()
        or query in str(p.get("username", "")).lower()
        or query in str(p.get("first_name", "")).lower()
        or query in str(p.get("last_name", "")).lower()
    ]


def list_banned_players():
    return [p for p in _all("users") if p.get("banned")]


# ============================================================
# WEEKLY TOURNAMENT ENTRIES
# ============================================================

def _weekly_entry_id(uid, session_key):
    return f"{session_key}_{uid}"


def grant_weekly_entry(uid, session_key=None, admin_id=None):
    uid = _user_id(uid)
    session_key = str(session_key or current_weekly_session())
    entry_id = _weekly_entry_id(uid, session_key)
    ref = _doc("weekly_entries", entry_id)

    if ref.get().exists:
        return True

    ref.set({
        "entry_id": entry_id,
        "telegram_id": uid,
        "session_key": session_key,
        "granted_at": timestamp(),
        "granted_by": str(admin_id) if admin_id is not None else None,
    })

    record_admin_activity(
        admin_id, "grant_weekly_entry", uid,
        {"session_key": session_key}
    )
    return True


def revoke_weekly_entry(uid, session_key=None):
    uid = _user_id(uid)
    session_key = str(session_key or current_weekly_session())
    entry_id = _weekly_entry_id(uid, session_key)
    ref = _doc("weekly_entries", entry_id)

    if ref.get().exists:
        ref.delete()
        return True

    return False


def has_weekly_entry(uid, session_key=None):
    uid = _user_id(uid)
    session_key = str(session_key or current_weekly_session())
    return _doc("weekly_entries", _weekly_entry_id(uid, session_key)).get().exists


def list_weekly_entries(session_key=None):
    session_key = str(session_key or current_weekly_session())
    return [
        entry for entry in _all("weekly_entries")
        if str(entry.get("session_key")) == session_key
    ]


def weekly_entries_diagnostic():
    all_entries = _all("weekly_entries")
    current_key = current_weekly_session()
    current_entries = [
        e for e in all_entries
        if str(e.get("session_key")) == current_key
    ]

    return {
        "current_session": current_key,
        "weekly_entries_total": len(all_entries),
        "weekly_entries_current": len(current_entries),
        "weekly_session_records": sorted(
            list({str(e.get("session_key")) for e in all_entries})
        ),
    }


# ============================================================
# MONTHLY MEMBERSHIPS
# ============================================================

def _monthly_membership_id(uid, month_key):
    return f"{month_key}_{uid}"


def grant_monthly_access(uid, month_key=None, admin_id=None):
    uid = _user_id(uid)
    month_key = _period_key(month_key)
    membership_id = _monthly_membership_id(uid, month_key)

    _doc("monthly_memberships", membership_id).set({
        "membership_id": membership_id,
        "telegram_id": uid,
        "month_key": month_key,
        "price_stars": MONTHLY_STARS,
        "granted_at": timestamp(),
        "granted_by": str(admin_id) if admin_id is not None else None,
        "expires_at": month_end_datetime(month_key),
        "source": "admin",
    }, merge=True)

    record_admin_activity(
        admin_id, "grant_monthly_access", uid,
        {"month_key": month_key}
    )
    return True


def grant_monthly_membership(uid, admin_id=None, month_key=None):
    return grant_monthly_access(uid, month_key, admin_id)


def revoke_monthly_access(uid, month_key=None):
    uid = _user_id(uid)
    month_key = _period_key(month_key)
    membership_id = _monthly_membership_id(uid, month_key)
    ref = _doc("monthly_memberships", membership_id)

    if ref.get().exists:
        ref.delete()
        return True

    return False


def has_monthly_access(uid, month_key=None):
    uid = _user_id(uid)
    month_key = _period_key(month_key)
    membership_id = _monthly_membership_id(uid, month_key)
    snap = _doc("monthly_memberships", membership_id).get()

    if not snap.exists:
        return False

    data = snap.to_dict() or {}
    expires = data.get("expires_at")

    if expires and hasattr(expires, "timestamp"):
        return expires >= timestamp()

    return True


def list_monthly_members(month_key=None):
    month_key = _period_key(month_key)
    return [
        m for m in _all("monthly_memberships")
        if str(m.get("month_key")) == month_key
    ]


# ============================================================
# SCORES AND LEADERBOARDS
# ============================================================

def save_score(uid, score, mode="weekly", session_key=None, username=None):
    uid = _user_id(uid)
    score = _as_int(score)

    if mode == "free":
        return False

    if mode == "weekly":
        session_key = str(session_key or current_weekly_session())
        if not has_weekly_entry(uid, session_key):
            return False

    elif mode == "monthly":
        session_key = str(session_key or current_month_key())
        if not has_monthly_access(uid, session_key):
            return False

    else:
        return False

    score_id = f"{mode}_{session_key}_{uid}_{int(now_utc().timestamp() * 1000)}"

    _doc("scores", score_id).set({
        "score_id": score_id,
        "telegram_id": uid,
        "username": username,
        "score": score,
        "mode": mode,
        "session_key": session_key,
        "created_at": timestamp(),
    })

    return True


def list_tournament_scores(mode="weekly", session_key=None, limit=100):
    if mode == "weekly":
        session_key = str(session_key or current_weekly_session())
    elif mode == "monthly":
        session_key = str(session_key or current_month_key())
    else:
        return []

    scores = [
        s for s in _all("scores")
        if s.get("mode") == mode
        and str(s.get("session_key")) == session_key
    ]

    scores.sort(key=lambda s: _as_int(s.get("score")), reverse=True)
    return scores[:limit]



def _score_recorded_time(row):
    """Convert a score's created_at value into a sortable timestamp."""
    value = row.get("created_at")

    if value is None:
        return float("inf")

    try:
        if isinstance(value, datetime):
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            return value.timestamp()

        if isinstance(value, str):
            value = value.strip().replace("Z", "+00:00")
            parsed = datetime.fromisoformat(value)

            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)

            return parsed.timestamp()

    except (ValueError, TypeError, OverflowError):
        pass

    return float("inf")


def list_tournament_player_rankings(
    mode="weekly",
    session_key=None,
    limit=100
):
    """
    Return one best score per Telegram player.

    Rules:
    1. Each Telegram ID appears only once.
    2. Highest score is retained.
    3. If scores are equal, the earliest recorded score wins.
    4. Remaining ties are ordered by Telegram ID.
    """
    rows = list_tournament_scores(
        mode=mode,
        session_key=session_key,
        limit=10000
    )

    best_by_player = {}

    for row in rows:
        uid = str(row.get("telegram_id", "")).strip()

        if not uid:
            continue

        score = _as_int(row.get("score"))
        recorded_time = _score_recorded_time(row)

        existing = best_by_player.get(uid)

        if existing is None:
            best_by_player[uid] = {
                "row": row,
                "score": score,
                "time": recorded_time,
            }
            continue

        if (
            score > existing["score"]
            or (
                score == existing["score"]
                and recorded_time < existing["time"]
            )
        ):
            best_by_player[uid] = {
                "row": row,
                "score": score,
                "time": recorded_time,
            }

    ranked = sorted(
        best_by_player.values(),
        key=lambda item: (
            -item["score"],
            item["time"],
            str(item["row"].get("telegram_id", ""))
        )
    )

    return [
        item["row"]
        for item in ranked[:max(0, limit)]
    ]


def get_tournament_ranking(mode="weekly", session_key=None, limit=100):
    return list_tournament_player_rankings(
        mode, session_key, limit
    )


def get_player_ranking(uid, mode="weekly", session_key=None):
    uid = _user_id(uid)

    scores = list_tournament_player_rankings(
        mode, session_key, 10000
    )

    for position, score in enumerate(scores, start=1):
        if str(score.get("telegram_id")) == uid:
            return position

    return None


def get_leaderboard(mode="weekly", session_key=None, limit=100):
    return list_tournament_player_rankings(
        mode, session_key, limit
    )


# ============================================================
# PAYMENTS
# ============================================================

def add_payment(uid, amount, currency="XTR", payment_id=None,
                provider_payment_charge_id=None, details=None):
    uid = _user_id(uid)
    payment_id = str(
        payment_id or provider_payment_charge_id
        or f"{uid}_{int(now_utc().timestamp() * 1000)}"
    )

    ref = _doc("payments", payment_id)

    if ref.get().exists:
        return False

    ref.set({
        "payment_id": payment_id,
        "telegram_id": uid,
        "amount": _as_int(amount),
        "currency": currency,
        "provider_payment_charge_id": provider_payment_charge_id,
        "details": details or {},
        "created_at": timestamp(),
    })
    return True


def record_monthly_payment(uid, telegram_charge_id,
                           provider_charge_id=None,
                           amount=MONTHLY_STARS,
                           currency="XTR"):
    uid = _user_id(uid)
    charge_id = str(telegram_charge_id)

    if _doc("payments", charge_id).get().exists:
        return False

    month_key = current_month_key()
    membership_id = _monthly_membership_id(uid, month_key)

    payment_ref = _doc("payments", charge_id)
    membership_ref = _doc("monthly_memberships", membership_id)
    user_ref = _doc("users", uid)

    batch = db().batch()

    batch.set(payment_ref, {
        "payment_id": charge_id,
        "telegram_id": uid,
        "amount": _as_int(amount),
        "currency": currency,
        "provider_payment_charge_id": provider_charge_id,
        "payment_type": "monthly_membership",
        "month_key": month_key,
        "created_at": timestamp(),
    })

    batch.set(membership_ref, {
        "membership_id": membership_id,
        "telegram_id": uid,
        "month_key": month_key,
        "price_stars": _as_int(amount),
        "granted_at": timestamp(),
        "expires_at": month_end_datetime(month_key),
        "source": "telegram_payment",
        "payment_id": charge_id,
    }, merge=True)

    batch.set(user_ref, {
        "telegram_id": uid,
        "premium_until": month_end_datetime(month_key),
        "updated_at": timestamp(),
    }, merge=True)

    batch.commit()
    return True


def create_mpesa_pending(internal_id, uid, phone, period_key,
                         payment_type="monthly_membership"):
    """Create a server-priced pending M-Pesa payment."""
    uid = _user_id(uid)
    is_weekly = payment_type == "weekly_entry"
    if payment_type not in ("monthly_membership", "weekly_entry"):
        raise ValueError("Unsupported M-Pesa payment type.")
    amount = 25 if is_weekly else 200
    fields = {
        "internal_id": internal_id,
        "telegram_id": uid,
        "phone": str(phone),
        "amount": amount,
        "currency": "KES",
        "payment_type": payment_type,
        "provider": "mpesa",
        "status": "pending",
        "created_at": timestamp(),
    }
    fields["session_key" if is_weekly else "month_key"] = str(period_key)
    _doc("mpesa_transactions", internal_id).set(fields)
    return True

def record_weekly_stars_payment(uid, telegram_charge_id,
                                provider_charge_id=None,
                                amount=50, currency="XTR",
                                session_key=None):
    """Record a verified 50-Star weekly entry exactly once."""
    uid = _user_id(uid)
    session_key = str(session_key or current_weekly_session())
    charge_id = str(telegram_charge_id or "")
    if not charge_id or _as_int(amount) != 50 or currency != "XTR":
        return False
    payment_ref = _doc("payments", charge_id)
    entry_ref = _doc("weekly_entries", _weekly_entry_id(uid, session_key))
    txn = db().transaction()

    @firestore.transactional
    def apply(tx):
        existing = tx.get(payment_ref)
        if existing.exists:
            return False
        tx.set(payment_ref, {
            "payment_id": charge_id,
            "telegram_id": uid,
            "amount": 50,
            "currency": "XTR",
            "provider_payment_charge_id": provider_charge_id,
            "payment_type": "weekly_entry",
            "session_key": session_key,
            "provider": "telegram_stars",
            "created_at": timestamp(),
        })
        tx.set(entry_ref, {
            "entry_id": _weekly_entry_id(uid, session_key),
            "telegram_id": uid,
            "session_key": session_key,
            "granted_at": timestamp(),
            "granted_by": None,
            "source": "telegram_stars",
            "payment_id": charge_id,
        }, merge=True)
        return True

    return apply(txn)


def update_mpesa_checkout(internal_id, checkout_id, merchant_id, response=None):
    _doc("mpesa_transactions", internal_id).set({
        "checkout_request_id": str(checkout_id),
        "merchant_request_id": str(merchant_id),
        "status": "initiated",
        "daraja_response": response or {},
        "updated_at": timestamp(),
    }, merge=True)
    return True


def fail_mpesa_pending(internal_id, reason):
    _doc("mpesa_transactions", internal_id).set({
        "status": "failed",
        "failure_reason": str(reason)[:500],
        "updated_at": timestamp(),
    }, merge=True)
    return True


def process_mpesa_callback(payload):
    """Validate a Daraja STK callback and grant access once only."""
    try:
        callback = payload["Body"]["stkCallback"]
        checkout_id = str(callback.get("CheckoutRequestID") or "")
        result_code = int(callback.get("ResultCode", -1))
    except (KeyError, TypeError, ValueError):
        return {"processed": False, "reason": "invalid_callback"}

    matches = list(
        db().collection("mpesa_transactions")
        .where("checkout_request_id", "==", checkout_id)
        .limit(1)
        .stream()
    )
    if not matches:
        return {"processed": False, "reason": "unknown_checkout"}

    pending_ref = matches[0].reference
    pending = matches[0].to_dict() or {}
    uid = str(pending.get("telegram_id", ""))
    month_key = str(pending.get("month_key", ""))
    payment_type = str(pending.get("payment_type") or "")
    if pending.get("provider") != "mpesa" or payment_type not in ("monthly_membership", "weekly_entry"):
        return {"processed": False, "reason": "wrong_payment_type"}
    is_weekly = payment_type == "weekly_entry"
    period_key = str(pending.get("session_key" if is_weekly else "month_key", ""))

    # Daraja callbacks must match both identifiers saved for this STK request.
    callback_merchant_id = str(callback.get("MerchantRequestID") or "")
    expected_merchant_id = str(pending.get("merchant_request_id") or "")
    if not callback_merchant_id or not expected_merchant_id or callback_merchant_id != expected_merchant_id:
        return {"processed": False, "reason": "merchant_request_mismatch"}

    # Never let a delayed failure callback downgrade a completed or reviewed payment.
    if result_code != 0:
        transaction = db().transaction()

        @firestore.transactional
        def apply_failure(txn):
            current = txn.get(pending_ref)
            if not current.exists:
                return False
            current_data = current.to_dict() or {}
            if current_data.get("status") not in ("pending", "initiated"):
                return False
            txn.update(pending_ref, {
                "status": "failed",
                "result_code": result_code,
                "result_description": str(callback.get("ResultDesc", ""))[:500],
                "updated_at": timestamp(),
            })
            return True

        applied = apply_failure(transaction)
        return {"processed": bool(applied), "success": False, "telegram_id": uid}

    metadata_items = (
        callback.get("CallbackMetadata", {}).get("Item", [])
        if isinstance(callback.get("CallbackMetadata"), dict) else []
    )
    metadata = {
        str(item.get("Name")): item.get("Value")
        for item in metadata_items
        if isinstance(item, dict) and item.get("Name")
    }
    from decimal import Decimal, InvalidOperation

    try:
        paid_amount_decimal = Decimal(str(metadata.get("Amount", "")))
    except (InvalidOperation, TypeError, ValueError):
        paid_amount_decimal = Decimal("-1")
    receipt = str(metadata.get("MpesaReceiptNumber") or "").strip()
    callback_phone = str(metadata.get("PhoneNumber") or "").strip()
    expected_phone = str(pending.get("phone") or "").strip()

    expected_amount = 25 if is_weekly else 200
    if paid_amount_decimal != Decimal(expected_amount) or not receipt:
        pending_ref.set({
            "status": "review_required",
            "result_code": result_code,
            "callback_amount": int(paid_amount_decimal),
            "updated_at": timestamp(),
        }, merge=True)
        return {"processed": False, "reason": "amount_or_receipt_mismatch"}

    if not callback_phone or not expected_phone or callback_phone != expected_phone:
        pending_ref.set({
            "status": "review_required",
            "callback_phone": callback_phone,
            "updated_at": timestamp(),
        }, merge=True)
        return {"processed": False, "reason": "phone_mismatch"}

    payment_id = "mpesa_" + receipt
    payment_ref = _doc("payments", payment_id)
    if is_weekly:
        entry_id = _weekly_entry_id(uid, period_key)
        entry_ref = _doc("weekly_entries", entry_id)
        transaction = db().transaction()

        @firestore.transactional
        def apply_weekly_success(txn):
            current = txn.get(pending_ref)
            if not current.exists:
                return False
            current_data = current.to_dict() or {}
            if current_data.get("status") in ("success", "failed", "review_required"):
                return False
            existing_payment = txn.get(payment_ref)
            if existing_payment.exists:
                txn.update(pending_ref, {
                    "status": "review_required",
                    "failure_reason": "M-Pesa receipt already linked to another payment.",
                    "updated_at": timestamp(),
                })
                return False
            txn.set(payment_ref, {
                "payment_id": payment_id,
                "telegram_id": uid,
                "amount": 25,
                "currency": "KES",
                "payment_type": "weekly_entry",
                "provider": "mpesa",
                "session_key": period_key,
                "mpesa_receipt": receipt,
                "checkout_request_id": checkout_id,
                "phone": callback_phone or expected_phone,
                "created_at": timestamp(),
            })
            txn.set(entry_ref, {
                "entry_id": entry_id,
                "telegram_id": uid,
                "session_key": period_key,
                "granted_at": timestamp(),
                "granted_by": None,
                "source": "mpesa",
                "payment_id": payment_id,
            }, merge=True)
            txn.update(pending_ref, {
                "status": "success",
                "mpesa_receipt": receipt,
                "callback_amount": int(paid_amount_decimal),
                "callback_phone": callback_phone,
                "paid_at": timestamp(),
                "updated_at": timestamp(),
            })
            return True

        applied = apply_weekly_success(transaction)
        return {
            "processed": bool(applied), "success": bool(applied),
            "telegram_id": uid, "payment_type": "weekly_entry",
            "session_key": period_key, "receipt": receipt,
        }

    membership_id = _monthly_membership_id(uid, period_key)
    membership_ref = _doc("monthly_memberships", membership_id)
    user_ref = _doc("users", uid)
    transaction = db().transaction()

    @firestore.transactional
    def apply_success(txn):
        current = txn.get(pending_ref)
        if not current.exists:
            return False
        current_data = current.to_dict() or {}
        if current_data.get("status") == "success":
            return False
        if current_data.get("status") in ("failed", "review_required"):
            return False

        existing_payment = txn.get(payment_ref)
        if existing_payment.exists:
            txn.update(pending_ref, {
                "status": "review_required",
                "failure_reason": "M-Pesa receipt already linked to another payment.",
                "updated_at": timestamp(),
            })
            return False

        txn.set(payment_ref, {
            "payment_id": payment_id,
            "telegram_id": uid,
            "amount": 200,
            "currency": "KES",
            "payment_type": "monthly_membership",
            "provider": "mpesa",
            "month_key": period_key,
            "mpesa_receipt": receipt,
            "checkout_request_id": checkout_id,
            "phone": callback_phone or expected_phone,
            "created_at": timestamp(),
        })
        txn.set(membership_ref, {
            "membership_id": membership_id,
            "telegram_id": uid,
            "month_key": month_key,
            "price_kes": 200,
            "granted_at": timestamp(),
            "expires_at": month_end_datetime(month_key),
            "source": "mpesa",
            "payment_id": payment_id,
        }, merge=True)
        txn.set(user_ref, {
            "telegram_id": uid,
            "premium_until": month_end_datetime(month_key),
            "updated_at": timestamp(),
        }, merge=True)
        txn.update(pending_ref, {
            "status": "success",
            "mpesa_receipt": receipt,
            "callback_amount": int(paid_amount_decimal),
            "callback_phone": callback_phone,
            "paid_at": timestamp(),
            "updated_at": timestamp(),
        })
        return True

    applied = apply_success(transaction)
    return {
        "processed": bool(applied),
        "success": bool(applied),
        "telegram_id": uid,
        "month_key": month_key,
        "receipt": receipt,
    }


def list_payments(limit=500):
    payments = _all("payments")
    payments.sort(
        key=lambda p: p.get("created_at") or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True
    )
    return payments[:limit]


def monthly_financials(month_key=None):
    month_key = _period_key(month_key)

    payments = [
        p for p in _all("payments")
        if str(p.get("month_key") or "") == month_key
        and p.get("payment_type") == "monthly_membership"
    ]
    stars_payments = [p for p in payments if p.get("currency") == "XTR"]
    mpesa_payments = [p for p in payments if p.get("currency") == "KES"]

    total_stars = sum(_as_int(p.get("amount")) for p in stars_payments)
    total_mpesa_kes = sum(_as_int(p.get("amount")) for p in mpesa_payments)
    prize_stars = total_stars * MONTHLY_PRIZE_PERCENT // 100
    retained_stars = total_stars * MONTHLY_RETAINED_PERCENT // 100

    return {
        "month_key": month_key,
        "payments_count": len(payments),
        "total_stars": total_stars,
        "prize_stars": prize_stars,
        "retained_stars": retained_stars,
        "mpesa_payments_count": len(mpesa_payments),
        "total_mpesa_kes": total_mpesa_kes,
        "prize_percent": MONTHLY_PRIZE_PERCENT,
        "retained_percent": MONTHLY_RETAINED_PERCENT,
    }


# ============================================================
# WEEKLY PRIZE FUNDING
# ============================================================

def add_weekly_funding(session_key, admin_id, amount,
                       currency="KES", funding_source="admin",
                       reference=None, notes=None):
    funding_id = f"{session_key}_{int(now_utc().timestamp() * 1000)}"

    _doc("weekly_prize_funding", funding_id).set({
        "funding_id": funding_id,
        "session_key": str(session_key),
        "admin_id": str(admin_id) if admin_id is not None else None,
        "amount": _as_int(amount),
        "currency": currency,
        "funding_source": funding_source,
        "reference": reference,
        "notes": notes,
        "created_at": timestamp(),
    })

    record_admin_activity(
        admin_id, "add_weekly_funding", session_key,
        {"amount": amount, "currency": currency}
    )
    return funding_id


def list_weekly_funding(session_key=None):
    items = _all("weekly_prize_funding")

    if session_key is not None:
        items = [
            i for i in items
            if str(i.get("session_key")) == str(session_key)
        ]

    items.sort(key=lambda i: i.get("created_at") or timestamp(), reverse=True)
    return items


def weekly_overview(session_key=None):
    session_key = str(session_key or current_weekly_session())

    funding = [
        f for f in _all("weekly_prize_funding")
        if str(f.get("session_key")) == session_key
    ]

    awards = [
        a for a in _all("prize_awards")
        if a.get("period_type") == "weekly"
        and str(a.get("period_key")) == session_key
    ]

    return {
        "session_key": session_key,
        "funding_total": sum(_as_int(f.get("amount")) for f in funding),
        "funding_count": len(funding),
        "awards_total": sum(_as_int(a.get("amount")) for a in awards),
        "awards_count": len(awards),
        "entries_count": len(list_weekly_entries(session_key)),
    }


# ============================================================
# PRIZE AWARDS
# ============================================================

def save_prize_award(period_type, period_key, position, telegram_id,
                     amount, currency="KES", status="pending", notes=None):
    award_id = (
        f"{period_type}_{period_key}_{position}_{telegram_id}"
    )

    _doc("prize_awards", award_id).set({
        "award_id": award_id,
        "period_type": str(period_type),
        "period_key": str(period_key),
        "position": _as_int(position),
        "telegram_id": _user_id(telegram_id),
        "amount": _as_int(amount),
        "currency": currency,
        "status": status,
        "notes": notes,
        "created_at": timestamp(),
        "paid_at": None,
        "payment_reference": None,
    }, merge=True)

    return award_id


def mark_prize_paid(award_id, payment_reference=None, notes=None):
    ref = _doc("prize_awards", str(award_id))

    if not ref.get().exists:
        return False

    ref.set({
        "status": "paid",
        "paid_at": timestamp(),
        "payment_reference": payment_reference,
        "notes": notes,
    }, merge=True)

    return True


def list_prize_awards(period_type=None, period_key=None):
    awards = _all("prize_awards")

    if period_type is not None:
        awards = [
            a for a in awards
            if str(a.get("period_type")) == str(period_type)
        ]

    if period_key is not None:
        awards = [
            a for a in awards
            if str(a.get("period_key")) == str(period_key)
        ]

    awards.sort(key=lambda a: _as_int(a.get("position")))
    return awards


# ============================================================
# TOURNAMENT HISTORY
# ============================================================

def list_tournament_periods(period_type="weekly", limit=100):
    if period_type == "weekly":
        scores = [s for s in _all("scores") if s.get("mode") == "weekly"]
        entries = _all("weekly_entries")
        keys = {
            str(s.get("session_key")) for s in scores
        } | {
            str(e.get("session_key")) for e in entries
        }

    elif period_type == "monthly":
        scores = [s for s in _all("scores") if s.get("mode") == "monthly"]
        members = _all("monthly_memberships")
        keys = {
            str(s.get("session_key")) for s in scores
        } | {
            str(m.get("month_key")) for m in members
        }

    else:
        return []

    current_key = (
        current_weekly_session()
        if period_type == "weekly"
        else current_month_key()
    )

    periods = sorted(keys, reverse=True)[:limit]

    return [
        {
            "period_key": key,
            "is_current": key == current_key,
        }
        for key in periods
    ]


def get_tournament_history(period_type, period_key):
    mode = str(period_type)
    period_key = str(period_key)

    scores = [
        s for s in _all("scores")
        if s.get("mode") == mode
        and str(s.get("session_key")) == period_key
    ]

    scores.sort(key=lambda s: _as_int(s.get("score")), reverse=True)

    if mode == "weekly":
        entries = [
            e for e in _all("weekly_entries")
            if str(e.get("session_key")) == period_key
        ]
        memberships = []
        funding = [
            f for f in _all("weekly_prize_funding")
            if str(f.get("session_key")) == period_key
        ]

    elif mode == "monthly":
        entries = []
        memberships = [
            m for m in _all("monthly_memberships")
            if str(m.get("month_key")) == period_key
        ]
        funding = []

    else:
        entries = []
        memberships = []
        funding = []

    awards = [
        a for a in _all("prize_awards")
        if str(a.get("period_type")) == mode
        and str(a.get("period_key")) == period_key
    ]

    current_key = (
        current_weekly_session()
        if mode == "weekly"
        else current_month_key()
    )

    return {
        "period_type": mode,
        "period_key": period_key,
        "is_current": period_key == current_key,
        "scores": scores,
        "entries": entries,
        "memberships": memberships,
        "funding": funding,
        "awards": awards,
    }


def get_tournament_history_summary():
    weekly_periods = list_tournament_periods("weekly")
    monthly_periods = list_tournament_periods("monthly")

    return {
        "weekly_periods_count": len(weekly_periods),
        "monthly_periods_count": len(monthly_periods),
        "weekly_periods": weekly_periods,
        "monthly_periods": monthly_periods,
    }


# ============================================================
# ADMIN ACTIVITY
# ============================================================

def record_admin_activity(admin_id, action, target_id=None, details=None):
    activity_id = str(int(now_utc().timestamp() * 1000))

    _doc("admin_activity", activity_id).set({
        "activity_id": activity_id,
        "admin_id": str(admin_id) if admin_id is not None else None,
        "action": str(action),
        "target_id": str(target_id) if target_id is not None else None,
        "details": details or {},
        "created_at": timestamp(),
    })

    return activity_id


def list_admin_activity(limit=200):
    items = _all("admin_activity")
    items.sort(key=lambda i: i.get("created_at") or timestamp(), reverse=True)
    return items[:limit]


# ============================================================
# STATISTICS AND PERFORMANCE
# ============================================================

def get_stats():
    users = _all("users")
    payments = _all("payments")
    scores = _all("scores")
    weekly_entries = _all("weekly_entries")
    monthly_members = _all("monthly_memberships")

    return {
        "users": len(users),
        "players": len(users),
        "banned": sum(1 for u in users if u.get("banned")),
        "payments": len(payments),
        "stars": sum(
            _as_int(p.get("amount"))
            for p in payments if p.get("currency") == "XTR"
        ),
        "scores": len(scores),
        "weekly_entries": len(weekly_entries),
        "monthly_members": len(monthly_members),
    }


def get_monthly_overview(month_key=None):
    month_key = _period_key(month_key)
    members = list_monthly_members(month_key)
    finances = monthly_financials(month_key)

    scores = [
        s for s in _all("scores")
        if s.get("mode") == "monthly"
        and str(s.get("session_key")) == month_key
    ]

    scores.sort(key=lambda s: _as_int(s.get("score")), reverse=True)

    return {
        "month_key": month_key,
        "members_count": len(members),
        "payments_count": finances["payments_count"],
        "total_stars": finances["total_stars"],
        "prize_stars": finances["prize_stars"],
        "retained_stars": finances["retained_stars"],
        "top_scores": scores[:5],
    }


def get_performance(mode="weekly", session_key=None):
    if mode == "weekly":
        session_key = str(session_key or current_weekly_session())
    elif mode == "monthly":
        session_key = str(session_key or current_month_key())
    else:
        return {
            "mode": mode,
            "session": None,
            "top_scores": [],
            "top_five": [],
        }

    scores = list_tournament_scores(mode, session_key, 100)

    return {
        "mode": mode,
        "session": session_key,
        "top_scores": scores,
        "top_five": scores[:5],
    }


# ============================================================
# INITIALIZE
# ============================================================

init_db()

