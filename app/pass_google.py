"""Google Wallet — Generic pass class/object + "Add to Google Wallet" JWT.

Model differs fundamentally from Apple:
  * one CLASS per card design (created once), one OBJECT per member
  * the user is given a signed JWT link; Google renders the save flow
  * updates are a PATCH on the object; Google propagates to devices
  * there is NO per-device registration callback -> no authoritative device count

Docs: https://developers.google.com/wallet/generic
"""

import json
import logging
import time
from typing import Dict, Optional

from . import config

logger = logging.getLogger(__name__)

SAVE_URL = "https://pay.google.com/gp/v/save/"
WALLET_SCOPE = "https://www.googleapis.com/auth/wallet_object.issuer"
API_BASE = "https://walletobjects.googleapis.com/walletobjects/v1"

_credentials = None


def _sa_info() -> Dict:
    return json.loads(config.get_secret(config.SECRET_GOOGLE_SA))


def _creds():
    """Service-account credentials scoped to the Wallet issuer API."""
    global _credentials
    if _credentials is None:
        from google.oauth2 import service_account
        _credentials = service_account.Credentials.from_service_account_info(
            _sa_info(), scopes=[WALLET_SCOPE]
        )
    return _credentials


def _session():
    from google.auth.transport.requests import AuthorizedSession
    return AuthorizedSession(_creds())


def class_id() -> str:
    return f"{config.GOOGLE_ISSUER_ID}.{config.GOOGLE_CLASS_SUFFIX}"


def object_id(member: Dict) -> str:
    return member.get("google_object_id") or f"{config.GOOGLE_ISSUER_ID}.{member['member_id']}"


# --- class ------------------------------------------------------------------

def ensure_class() -> str:
    """Create the Generic class once. Idempotent — a 409 means it already exists.

    Run this at deploy time, not per request.
    """
    config.require("GOOGLE_ISSUER_ID")
    cid = class_id()
    session = _session()

    response = session.get(f"{API_BASE}/genericClass/{cid}")
    if response.status_code == 200:
        return cid

    body = {
        "id": cid,
        "classTemplateInfo": {
            "cardTemplateOverride": {
                "cardRowTemplateInfos": [{
                    "twoItems": {
                        "startItem": {"firstValue": {"fields": [
                            {"fieldPath": "object.textModulesData['membership']"}]}},
                        "endItem": {"firstValue": {"fields": [
                            {"fieldPath": "object.textModulesData['status']"}]}},
                    }
                }]
            }
        },
    }
    created = session.post(f"{API_BASE}/genericClass", json=body)
    if created.status_code in (200, 409):
        return cid
    raise RuntimeError(f"genericClass create failed: {created.status_code} {created.text[:300]}")


# --- object -----------------------------------------------------------------

LIFETIME = "LIFETIME"
_LIFETIME_VALUES = {"LIFETIME", "NA", "N/A"}


def is_lifetime(expiry: str) -> bool:
    return str(expiry or "").strip().upper() in _LIFETIME_VALUES


def _fmt_date(iso: str, style: str = "%d %b %Y") -> str:
    if not iso:
        return ""
    if is_lifetime(iso):
        return LIFETIME
    from datetime import datetime
    try:
        return datetime.strptime(str(iso)[:10], "%Y-%m-%d").strftime(style)
    except ValueError:
        return str(iso)


def _object_body(member: Dict) -> Dict:
    """Generic pass mirroring the Apple layout and the approved mockup.

    Google's Generic pass renders: cardTitle, header (the prominent line),
    subheader, then textModulesData rows. Member name goes in `header` so it is the
    largest element, matching Apple's primaryFields.
    """
    status = (member.get("status") or "").upper()
    membership = member.get("membership_number", "")
    names = member.get("member_names") or []
    if isinstance(names, str):
        names = [names]
    name_value = ", ".join(names) if names else member.get("email", "")

    # MEMBER SINCE dropped from the card face to match Apple (see pass_apple).
    # Google's Generic pass has no reverse side, so it simply isn't displayed; the
    # value is still captured and stored in Firestore.
    text_modules = [
        {"id": "membership", "header": "MEMBERSHIP NUMBER", "body": membership},
        {"id": "status", "header": "MEMBERSHIP STATUS", "body": status},
    ]
    # Google's subheader carries the expiry; a lifetime card needs it stated on a
    # text module instead, because "Expires NA" reads badly.
    expiry_raw = member.get("expiry_date", "")
    if is_lifetime(expiry_raw):
        text_modules.append({"id": "expiry", "header": "EXPIRY DATE", "body": LIFETIME})

    body = {
        "id": object_id(member),
        "classId": class_id(),
        "state": "ACTIVE",  # Wallet object lifecycle, not membership status
        "hexBackgroundColor": config.CARD_BACKGROUND_HEX,
        "cardTitle": {"defaultValue": {"language": "en-AU", "value": config.ORG_NAME}},
        "header": {"defaultValue": {"language": "en-AU", "value": name_value}},
        "textModulesData": text_modules,
    }
    # No barcode: nothing scans these cards, and Google's own guidance is not to
    # show one without scanning capability.

    # Lifetime members get NO subheader and NO validTimeInterval - setting the latter
    # would make Wallet treat a never-expiring card as expired.
    expiry_display = _fmt_date(expiry_raw, "%d-%b-%Y")
    if expiry_display and not is_lifetime(expiry_raw):
        # Past tense for a lapsed membership: "Expires on" reads as though the card
        # is still valid.
        prefix = "Expired on" if status == "EXPIRED" else "Expires on"
        body["subheader"] = {"defaultValue": {"language": "en-AU",
                                             "value": f"{prefix} {expiry_display}"}}
        body["validTimeInterval"] = {
            "end": {"date": f"{str(expiry_raw)[:10]}T23:59:59.000Z"}
        }

    if config.CARD_LOGO_URL:
        body["logo"] = {
            "sourceUri": {"uri": config.CARD_LOGO_URL},
            "contentDescription": {"defaultValue": {"language": "en-AU",
                                                    "value": f"{config.ORG_NAME} logo"}},
        }

    # Member photo. Google's Generic pass has NO portrait slot equivalent to Apple's
    # thumbnail, so the photo goes in an image module and renders BELOW the fields
    # rather than beside them. Android will therefore not match the iOS layout.
    # Absent photo = key omitted entirely.
    from . import photos
    photo = photos.photo_url(membership)
    if photo:
        body["imageModulesData"] = [{
            "id": "member_photo",
            "mainImage": {
                "sourceUri": {"uri": photo},
                "contentDescription": {"defaultValue": {"language": "en-AU",
                                                        "value": "Member photo"}},
            },
        }]

    return body


def upsert_object(member: Dict) -> str:
    """Create or update the member's Wallet object. Google propagates a PATCH to
    every device holding the pass, which is the whole Day-2 update path.
    """
    config.require("GOOGLE_ISSUER_ID")
    session = _session()
    oid = object_id(member)
    body = _object_body(member)

    existing = session.get(f"{API_BASE}/genericObject/{oid}")
    if existing.status_code == 200:
        patched = session.patch(f"{API_BASE}/genericObject/{oid}", json=body)
        if patched.status_code != 200:
            raise RuntimeError(f"genericObject patch failed: {patched.status_code} {patched.text[:300]}")
        return oid

    created = session.post(f"{API_BASE}/genericObject", json=body)
    if created.status_code not in (200, 409):
        raise RuntimeError(f"genericObject create failed: {created.status_code} {created.text[:300]}")
    return oid


def save_link(member: Dict) -> str:
    """Mint the "Add to Google Wallet" URL.

    The JWT embeds the object inline, so the pass is created on save even if
    upsert_object has not run — but we still upsert so later PATCH updates work.
    """
    import jwt

    info = _sa_info()
    payload = {
        "iss": info["client_email"],
        "aud": "google",
        "typ": "savetowallet",
        "iat": int(time.time()),
        "origins": [config.SERVICE_BASE_URL] if config.SERVICE_BASE_URL else [],
        "payload": {"genericObjects": [_object_body(member)]},
    }
    token = jwt.encode(payload, info["private_key"], algorithm="RS256")
    return SAVE_URL + token
