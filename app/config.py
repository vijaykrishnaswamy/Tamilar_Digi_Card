"""Configuration and secret access.

Everything sensitive lives in Secret Manager (LLD section 9). Env vars carry only
non-secret identifiers so the service can start without a secret round-trip.

Secrets are cached per-process: Cloud Run keeps an instance warm across requests,
and Secret Manager bills per access.
"""

import functools
import logging
import os

logger = logging.getLogger(__name__)

PROJECT_ID = os.environ.get("GCP_PROJECT", "")

# --- non-secret identifiers -------------------------------------------------
# Public HTTPS base for this service. Apple embeds it as webServiceURL inside the
# pass and calls back to it, so it MUST be the real external URL, not localhost.
SERVICE_BASE_URL = os.environ.get("SERVICE_BASE_URL", "").rstrip("/")

APPLE_TEAM_ID = os.environ.get("APPLE_TEAM_ID", "")
APPLE_PASS_TYPE_ID = os.environ.get("APPLE_PASS_TYPE_ID", "")   # pass.au.org.example
APPLE_APNS_KEY_ID = os.environ.get("APPLE_APNS_KEY_ID", "")
APPLE_APNS_HOST = os.environ.get("APPLE_APNS_HOST", "api.push.apple.com")

GOOGLE_ISSUER_ID = os.environ.get("GOOGLE_ISSUER_ID", "")
GOOGLE_CLASS_SUFFIX = os.environ.get("GOOGLE_CLASS_SUFFIX", "membership")

ORG_NAME = os.environ.get("ORG_NAME", "Membership")
SUPPORT_EMAIL = os.environ.get("SUPPORT_EMAIL", "")
EMAIL_SENDER = os.environ.get("EMAIL_SENDER", "")

# LLD 3.2 / decision 5: block the 3rd device. Count of active devices allowed.
MAX_DEVICES_PER_MEMBER = int(os.environ.get("MAX_DEVICES_PER_MEMBER", "2"))

# Card colours carry the status, because neither wallet allows per-field bold or
# colour (LLD section 8). ACTIVE = green card, EXPIRED = red card.
STATUS_COLOURS = {
    "ACTIVE": {"background": "rgb(21,128,61)", "hex": "#15803D"},
    "EXPIRED": {"background": "rgb(185,28,28)", "hex": "#B91C1C"},
}

VALID_STATUSES = ("ACTIVE", "EXPIRED")


# --- secrets ----------------------------------------------------------------

SECRET_APPLE_CERT = "apple-pass-cert-pem"       # Pass Type ID cert (PEM)
SECRET_APPLE_KEY = "apple-pass-key-pem"         # its private key (PEM)
SECRET_APPLE_WWDR = "apple-wwdr-pem"            # Apple WWDR intermediate (PEM)
SECRET_APNS_KEY = "apple-apns-authkey-p8"       # APNs .p8 auth key
SECRET_GOOGLE_SA = "google-wallet-sa-json"      # Wallet service-account JSON
SECRET_API_KEY = "cards-api-key"                # shared key for POST /cards


@functools.lru_cache(maxsize=32)
def get_secret(name: str, version: str = "latest") -> str:
    """Fetch a secret payload as text. Cached for the life of the instance."""
    if not PROJECT_ID:
        raise RuntimeError("GCP_PROJECT is not set; cannot read secrets")
    from google.cloud import secretmanager

    client = secretmanager.SecretManagerServiceClient()
    path = f"projects/{PROJECT_ID}/secrets/{name}/versions/{version}"
    response = client.access_secret_version(request={"name": path})
    return response.payload.data.decode("utf-8")


def get_secret_bytes(name: str, version: str = "latest") -> bytes:
    return get_secret(name, version).encode("utf-8")


def require(*names: str) -> None:
    """Fail fast on missing configuration rather than 500-ing mid-request."""
    missing = [n for n in names if not globals().get(n)]
    if missing:
        raise RuntimeError(f"Missing required configuration: {', '.join(missing)}")
