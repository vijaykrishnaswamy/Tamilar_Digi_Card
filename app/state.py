"""Firestore repository â€” the single source of truth (LLD section 6).

Schema:
    members/{memberId}                      memberId = sha256(lower(email))[:32]
    members/{memberId}/devices/{deviceId}
    events/{eventId}

Deliberately the ONLY module that touches Firestore, so the "stateful engine" in
the original LLD has exactly one implementation. Note the original design said
state would live "locally" â€” it cannot: Cloud Run instances are ephemeral and
horizontally scaled, so state is external by necessity.
"""

import hashlib
import logging
import secrets
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from . import config

logger = logging.getLogger(__name__)

# google-cloud-firestore is imported lazily inside db()/_filter() so that the pure
# logic (ingest, device_limit, pass building) can be imported and unit-tested
# without GCP libraries or credentials. Also trims Cloud Run cold-start time.

MEMBERS = "members"
DEVICES = "devices"
EVENTS = "events"

# event types (LLD section 6)
EV_EMAIL_SENT = "EMAIL_SENT"
EV_CLICKED = "CLICKED"
EV_ADDED = "ADDED"
EV_UPDATE_PUSHED = "UPDATE_PUSHED"
EV_BLOCKED = "BLOCKED"

_client = None


def db():
    """Lazily create the Firestore client (one per instance)."""
    global _client
    if _client is None:
        from google.cloud import firestore
        _client = firestore.Client(project=config.PROJECT_ID or None)
    return _client


def _filter(field: str, op: str, value):
    from google.cloud.firestore import FieldFilter
    return FieldFilter(field, op, value)


def member_id(email: str) -> str:
    """Stable document id from the email. Email is the user key per LLD 3.1."""
    return hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()[:32]


def _now():
    return datetime.now(timezone.utc)


# --- members ----------------------------------------------------------------

def upsert_member(email: str, membership_number: str, status: str,
                  member_names: Optional[List[str]] = None,
                  expiry_date: str = "") -> Dict[str, Any]:
    """Create or update a member. Returns the stored document plus two flags:
    `_is_new` and `_status_changed`, which drive whether we send an invite email
    or push an update to existing passes (LLD flow 1).
    """
    email = email.strip().lower()
    mid = member_id(email)
    ref = db().collection(MEMBERS).document(mid)
    snapshot = ref.get()

    if snapshot.exists:
        existing = snapshot.to_dict() or {}
        # Any visible card change must trigger a push, not just status.
        status_changed = (
            existing.get("status") != status
            or (member_names and existing.get("member_names") != member_names)
            or (expiry_date and existing.get("expiry_date") != expiry_date)
        )
        payload = {
            "membership_number": membership_number,
            "status": status,
            "member_names": member_names or existing.get("member_names") or [],
            "expiry_date": expiry_date or existing.get("expiry_date") or "",
            "updated_at": _now(),
        }
        ref.update(payload)
        merged = {**existing, **payload, "member_id": mid}
        merged["_is_new"] = False
        merged["_status_changed"] = status_changed
        return merged

    # link_token is unguessable and is the ONLY thing in the /add URL, so cards
    # cannot be enumerated from an email or membership number (LLD section 9).
    document = {
        "member_id": mid,
        "email": email,
        "membership_number": membership_number,
        "status": status,
        "member_names": member_names or [],
        "expiry_date": expiry_date or "",
        "link_token": secrets.token_urlsafe(32),
        "apple_serial": f"{mid}",
        "google_object_id": f"{config.GOOGLE_ISSUER_ID}.{mid}" if config.GOOGLE_ISSUER_ID else "",
        "created_at": _now(),
        "updated_at": _now(),
    }
    ref.set(document)
    document["_is_new"] = True
    document["_status_changed"] = False
    return document


def get_member(mid: str) -> Optional[Dict[str, Any]]:
    snapshot = db().collection(MEMBERS).document(mid).get()
    if not snapshot.exists:
        return None
    return {**(snapshot.to_dict() or {}), "member_id": mid}


def get_member_by_token(token: str) -> Optional[Dict[str, Any]]:
    """Resolve the /add?token link back to a member."""
    if not token:
        return None
    hits = (db().collection(MEMBERS)
            .where(filter=_filter("link_token", "==", token))
            .limit(1).stream())
    for snapshot in hits:
        return {**(snapshot.to_dict() or {}), "member_id": snapshot.id}
    return None


def get_member_by_apple_serial(serial: str) -> Optional[Dict[str, Any]]:
    """Apple's web service identifies passes by serialNumber only."""
    return get_member(serial)


def iter_members() -> Iterable[Dict[str, Any]]:
    for snapshot in db().collection(MEMBERS).stream():
        yield {**(snapshot.to_dict() or {}), "member_id": snapshot.id}


# --- devices ----------------------------------------------------------------

def active_devices(mid: str) -> List[Dict[str, Any]]:
    hits = (db().collection(MEMBERS).document(mid).collection(DEVICES)
            .where(filter=_filter("active", "==", True)).stream())
    return [{**(s.to_dict() or {}), "device_id": s.id} for s in hits]


def active_device_count(mid: str) -> int:
    return len(active_devices(mid))


def find_device(mid: str, device_id: str) -> Optional[Dict[str, Any]]:
    snapshot = (db().collection(MEMBERS).document(mid)
                .collection(DEVICES).document(device_id).get())
    if not snapshot.exists:
        return None
    return {**(snapshot.to_dict() or {}), "device_id": device_id}


def register_device(mid: str, device_id: str, platform: str, os_name: str = "",
                    apple_device_lib_id: str = "", apple_push_token: str = "") -> None:
    """Idempotent: re-adding on a device already recorded must not consume a
    second slot. Apple can call register repeatedly for the same device.
    """
    ref = (db().collection(MEMBERS).document(mid)
           .collection(DEVICES).document(device_id))
    snapshot = ref.get()
    payload = {
        "platform": platform,
        "os": os_name,
        "active": True,
        "updated_at": _now(),
    }
    if apple_device_lib_id:
        payload["apple_device_lib_id"] = apple_device_lib_id
    if apple_push_token:
        payload["apple_push_token"] = apple_push_token

    if snapshot.exists:
        ref.update(payload)
    else:
        payload["added_at"] = _now()
        ref.set(payload)


def deactivate_device(mid: str, device_id: str) -> None:
    ref = (db().collection(MEMBERS).document(mid)
           .collection(DEVICES).document(device_id))
    if ref.get().exists:
        ref.update({"active": False, "updated_at": _now()})


def reset_devices(mid: str) -> int:
    """Support reset (LLD flow 5) â€” frees the member's device slots.
    Caller MUST have verified the request came from the registered email.
    """
    count = 0
    for device in active_devices(mid):
        deactivate_device(mid, device["device_id"])
        count += 1
    return count


def apple_push_tokens(mid: str) -> List[str]:
    return [d["apple_push_token"] for d in active_devices(mid)
            if d.get("platform") == "APPLE" and d.get("apple_push_token")]


# --- events -----------------------------------------------------------------

def log_event(mid: str, event_type: str, delivery_status: str = "",
              device_id: str = "", os_name: str = "") -> str:
    """events/{eventId} â€” the original design's IndexId is the Firestore doc id."""
    _, ref = db().collection(EVENTS).add({
        "member_id": mid,
        "type": event_type,
        "delivery_status": delivery_status,
        "device_id": device_id,
        "os": os_name,
        "ts": _now(),
    })
    return ref.id

