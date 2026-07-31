"""Apple Wallet — .pkpass build, PKCS#7 signing, APNs push (LLD module `pass_apple`).

A .pkpass is a ZIP containing:
    pass.json      the pass definition
    icon.png       required, plus optional logo/strip images
    manifest.json  {filename: SHA-1 hex} for every other file
    signature      detached PKCS#7 signature OVER manifest.json

Signing chain: Pass Type ID certificate + its private key + the Apple WWDR
intermediate. All three come from Secret Manager.

Update mechanism: change state -> APNs push (empty payload, topic = passTypeId) ->
the device calls back to GET /v1/passes/... to pull the new pass. The push carries
no data; it is only a nudge.
"""

import hashlib
import io
import json
import logging
import time
import zipfile
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.serialization import pkcs7

from . import config

logger = logging.getLogger(__name__)

# Apple REQUIRES icon.png — without it the pass silently fails to open. Ship a 1x1
# transparent placeholder so the service works before real artwork exists; callers
# override it by passing images={"icon.png": ..., "logo.png": ...}.
_PLACEHOLDER_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8"
    "/wcAAwAB/epv2AAAAABJRU5ErkJggg=="
)


def _placeholder_png() -> bytes:
    import base64
    return base64.b64decode(_PLACEHOLDER_PNG_B64)


def build_pass_json(member: Dict, base_url: str, auth_token: str) -> Dict:
    """pass.json for a generic membership card.

    Status is conveyed by the CARD BACKGROUND COLOUR, not by bold coloured text —
    Apple does not allow per-field colour or weight (LLD section 8).
    """
    status = (member.get("status") or "").upper()
    colours = config.STATUS_COLOURS.get(status, config.STATUS_COLOURS["EXPIRED"])

    return {
        "formatVersion": 1,
        "passTypeIdentifier": config.APPLE_PASS_TYPE_ID,
        "teamIdentifier": config.APPLE_TEAM_ID,
        "serialNumber": member["apple_serial"],
        "organizationName": config.ORG_NAME,
        "description": f"{config.ORG_NAME} membership card",
        # Devices call these back to register and to pull updates.
        "webServiceURL": f"{base_url}/v1",
        "authenticationToken": auth_token,
        "backgroundColor": colours["background"],
        "foregroundColor": "rgb(255,255,255)",
        "labelColor": "rgb(255,255,255)",
        "generic": {
            "primaryFields": [
                {"key": "member", "label": "MEMBERSHIP NO",
                 "value": member.get("membership_number", "")},
            ],
            "secondaryFields": [
                {"key": "status", "label": "STATUS", "value": status},
            ],
            "backFields": [
                {"key": "email", "label": "Registered email", "value": member.get("email", "")},
                {"key": "support", "label": "Support",
                 "value": config.SUPPORT_EMAIL or "Contact your administrator"},
                {"key": "updated", "label": "Last updated",
                 "value": datetime.now(timezone.utc).strftime("%d %b %Y")},
            ],
        },
        "barcodes": [{
            "format": "PKBarcodeFormatQR",
            "message": member.get("membership_number", ""),
            "messageEncoding": "iso-8859-1",
        }],
    }


def auth_token_for(member: Dict) -> str:
    """Per-pass authentication token. Apple sends it as
    `Authorization: ApplePass <token>` on every web-service call.

    Derived from link_token so it is stable across rebuilds but is not the same
    value as the public /add token.
    """
    seed = f"{member.get('link_token','')}:{member.get('member_id','')}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def _sign_manifest(manifest_bytes: bytes) -> bytes:
    """Detached PKCS#7 (DER) signature over manifest.json."""
    cert_pem = config.get_secret_bytes(config.SECRET_APPLE_CERT)
    key_pem = config.get_secret_bytes(config.SECRET_APPLE_KEY)
    wwdr_pem = config.get_secret_bytes(config.SECRET_APPLE_WWDR)

    certificate = x509.load_pem_x509_certificate(cert_pem)
    private_key = serialization.load_pem_private_key(key_pem, password=None)
    wwdr = x509.load_pem_x509_certificate(wwdr_pem)

    return (
        pkcs7.PKCS7SignatureBuilder()
        .set_data(manifest_bytes)
        .add_signer(certificate, private_key, hashes.SHA256())
        .add_certificate(wwdr)
        .sign(serialization.Encoding.DER, [pkcs7.PKCS7Options.DetachedSignature])
    )


def build_pkpass(member: Dict, images: Optional[Dict[str, bytes]] = None) -> bytes:
    """Assemble and sign the .pkpass. Returns the zip bytes."""
    config.require("APPLE_PASS_TYPE_ID", "APPLE_TEAM_ID", "SERVICE_BASE_URL")

    pass_json = build_pass_json(member, config.SERVICE_BASE_URL, auth_token_for(member))

    files: Dict[str, bytes] = {
        "pass.json": json.dumps(pass_json, separators=(",", ":")).encode("utf-8"),
    }
    supplied = images or {}
    if "icon.png" not in supplied:
        supplied = {**supplied, "icon.png": _placeholder_png()}
    files.update(supplied)

    # manifest.json is SHA-1 per file. SHA-1 is mandated by Apple's format here;
    # it is an integrity manifest, not a security control (the PKCS#7 signature is).
    manifest = {name: hashlib.sha1(data).hexdigest() for name, data in files.items()}
    manifest_bytes = json.dumps(manifest, separators=(",", ":")).encode("utf-8")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
        archive.writestr("manifest.json", manifest_bytes)
        archive.writestr("signature", _sign_manifest(manifest_bytes))
    return buffer.getvalue()


# --- APNs -------------------------------------------------------------------

def _apns_jwt() -> str:
    """ES256 provider token for APNs, valid 1h (Apple's max is 60 min)."""
    import jwt

    config.require("APPLE_APNS_KEY_ID", "APPLE_TEAM_ID")
    key_p8 = config.get_secret(config.SECRET_APNS_KEY)
    now = int(time.time())
    return jwt.encode(
        {"iss": config.APPLE_TEAM_ID, "iat": now},
        key_p8,
        algorithm="ES256",
        headers={"kid": config.APPLE_APNS_KEY_ID},
    )


def push_updates(push_tokens: List[str]) -> Tuple[int, List[str]]:
    """Nudge devices to re-fetch the pass. Empty payload by design — pass pushes
    carry no content. Returns (sent_ok, failures).

    A 410 means the token is dead; the caller should deactivate that device.
    """
    if not push_tokens:
        return 0, []

    import httpx

    token = _apns_jwt()
    headers = {
        "authorization": f"bearer {token}",
        "apns-topic": config.APPLE_PASS_TYPE_ID,  # for passes the topic IS the pass type id
        "apns-push-type": "background",
        "apns-priority": "5",
    }

    sent, failures = 0, []
    with httpx.Client(http2=True, timeout=20.0) as client:
        for push_token in push_tokens:
            url = f"https://{config.APPLE_APNS_HOST}/3/device/{push_token}"
            try:
                response = client.post(url, headers=headers, json={})
                if response.status_code == 200:
                    sent += 1
                else:
                    failures.append(f"{push_token[:12]}...: {response.status_code} {response.text[:120]}")
            except Exception as exc:  # noqa: BLE001 - one bad token must not stop the rest
                failures.append(f"{push_token[:12]}...: {exc}")

    if failures:
        logger.warning("APNs: %s sent, %s failed", sent, len(failures))
        for line in failures[:10]:
            logger.warning("  %s", line)
    return sent, failures
