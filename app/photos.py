"""Member photo lookup and sizing (optional per member).

Photos are keyed on the MEMBERSHIP NUMBER, matching how they are named on disk
(e.g. 1000207.jpeg -> membership_number "1000207"). That is deliberately NOT the
internal member_id (a sha256 of the email) - the operator names files from the
membership number they can see.

Storage, in the order they are tried: a local directory (development only), a GCS
bucket (upload with tools/upload_photos.py), then PHOTO_BASE_URL - the same public
HTTPS location Google Wallet is handed. Apple embeds image bytes in the .pkpass
rather than a URL, so when only PHOTO_BASE_URL is configured the bytes are fetched
here instead of being referenced.

Absent photo = no photo on the card. Never an error, never a placeholder face.

Sizing, per Apple's generic pass thumbnail slot:
    thumbnail.png      90x90    thumbnail@2x  180x180    thumbnail@3x  270x270
Apple crops to a square, so we centre-crop rather than letterbox - a squashed
portrait looks worse than a tight crop.
"""

import functools
import io
import logging
import re
from pathlib import Path
from typing import Dict, Optional

from . import config

logger = logging.getLogger(__name__)

# Apple thumbnail sizes (points -> px at 1x/2x/3x)
THUMB_SIZES = {"thumbnail.png": 90, "thumbnail@2x.png": 180, "thumbnail@3x.png": 270}
# Single larger square for Google, which fetches over HTTPS
GOOGLE_SIZE = 400

# Accepted source extensions, in preference order
EXTENSIONS = (".jpg", ".jpeg", ".png", ".JPG", ".JPEG", ".PNG")

# Membership numbers form part of an object path, so keep them to safe characters
# and prevent traversal (e.g. "../../secret").
_SAFE_KEY = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def _safe(membership_number: str) -> Optional[str]:
    key = (membership_number or "").strip()
    if not key or not _SAFE_KEY.match(key):
        if key:
            logger.warning("refusing unsafe photo key %r", key)
        return None
    return key


def _read_local(key: str) -> Optional[bytes]:
    if not config.PHOTO_LOCAL_DIR:
        return None
    base = Path(config.PHOTO_LOCAL_DIR)
    for ext in EXTENSIONS:
        candidate = base / f"{key}{ext}"
        if candidate.exists():
            return candidate.read_bytes()
    return None


def _read_gcs(key: str) -> Optional[bytes]:
    if not config.PHOTO_BUCKET:
        return None
    from google.cloud import storage

    client = storage.Client()
    bucket = client.bucket(config.PHOTO_BUCKET)
    for ext in EXTENSIONS:
        blob = bucket.blob(f"{config.PHOTO_PREFIX}{key}{ext}")
        if blob.exists():
            return blob.download_as_bytes()
    return None


def _read_url(key: str) -> Optional[bytes]:
    """Download the photo from PHOTO_BASE_URL.

    Extension order matches remote_photo_url so Apple and Google resolve the same
    file for a member who happens to have more than one uploaded.
    """
    if not config.PHOTO_BASE_URL:
        return None
    import requests

    for ext in (".jpg", ".jpeg", ".png"):
        url = f"{config.PHOTO_BASE_URL}/{key}{ext}"
        try:
            response = requests.get(url, timeout=10, allow_redirects=True)
            if response.status_code == 200:
                return response.content
            logger.info("no photo at %s (%s)", url, response.status_code)
        except Exception as exc:  # noqa: BLE001 - a photo must never block a pass
            logger.warning("photo fetch failed for %s: %s", url, exc)
    return None


@functools.lru_cache(maxsize=512)
def source_bytes(membership_number: str) -> Optional[bytes]:
    """Raw photo bytes, or None when the member has no photo.

    Cached per instance: the same member re-fetching their pass after an update
    should not re-download the image.
    """
    key = _safe(membership_number)
    if not key:
        return None
    try:
        return _read_local(key) or _read_gcs(key) or _read_url(key)
    except Exception as exc:  # noqa: BLE001 - a photo problem must never block a pass
        logger.warning("photo lookup failed for %s: %s", key, exc)
        return None


def _square(raw: bytes, size: int) -> bytes:
    """Centre-crop to a square and resize to `size`x`size` PNG."""
    from PIL import Image

    image = Image.open(io.BytesIO(raw)).convert("RGB")
    width, height = image.size
    edge = min(width, height)
    left = (width - edge) // 2
    top = (height - edge) // 3  # bias upward: faces sit above centre
    image = image.crop((left, top, left + edge, top + edge))
    image = image.resize((size, size), Image.LANCZOS)

    buffer = io.BytesIO()
    image.save(buffer, "PNG", optimize=True)
    return buffer.getvalue()


def apple_thumbnails(membership_number: str) -> Dict[str, bytes]:
    """{filename: png bytes} for the .pkpass, or {} when there is no photo."""
    raw = source_bytes(membership_number)
    if not raw:
        return {}
    try:
        return {name: _square(raw, size) for name, size in THUMB_SIZES.items()}
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not render thumbnails for %s: %s", membership_number, exc)
        return {}


def google_square(membership_number: str) -> Optional[bytes]:
    """Single square PNG served over HTTPS for Google Wallet."""
    raw = source_bytes(membership_number)
    if not raw:
        return None
    try:
        return _square(raw, GOOGLE_SIZE)
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not render google photo for %s: %s", membership_number, exc)
        return None


def has_photo(membership_number: str) -> bool:
    return source_bytes(membership_number) is not None


# --- Google: private bucket + V4 signed URL ---------------------------------
# The bucket stays private. Google's servers fetch a time-limited signed URL, so
# photos are not enumerable by membership number the way a public /photo/<n>.png
# endpoint would be.

def rendered_key(key: str) -> str:
    """Object path for the square PNG Google fetches."""
    return f"{config.PHOTO_PREFIX}rendered/{key}.png"


def _signing_credentials():
    """Credentials able to sign a URL.

    Cloud Run's metadata credentials have no private key, so signing uses the
    Wallet service-account JSON already held in Secret Manager.
    """
    import json
    from google.oauth2 import service_account
    info = json.loads(config.get_secret(config.SECRET_GOOGLE_SA))
    return service_account.Credentials.from_service_account_info(info)


def ensure_rendered(membership_number: str) -> Optional[str]:
    """Make sure the square PNG exists in GCS. Returns its object key, or None.

    Rendered once and cached in the bucket; later calls only check existence.
    """
    key = _safe(membership_number)
    if not key or not config.PHOTO_BUCKET:
        return None
    data = google_square(key)
    if not data:
        return None

    from google.cloud import storage
    blob = storage.Client().bucket(config.PHOTO_BUCKET).blob(rendered_key(key))
    if not blob.exists():
        blob.upload_from_string(data, content_type="image/png")
        logger.info("rendered member photo cached at %s", rendered_key(key))
    return rendered_key(key)


@functools.lru_cache(maxsize=512)
def remote_photo_url(key: str) -> str:
    """Public HTTPS photo URL for a membership number, or "" when absent.

    Used when PHOTO_BASE_URL is configured (e.g. a GitHub raw prefix). The URL is
    probed with a HEAD so a member without a photo yields no image key at all,
    rather than a card referencing a 404.
    """
    if not config.PHOTO_BASE_URL:
        return ""
    import requests

    # A streamed GET, not HEAD: raw.githubusercontent.com does not answer HEAD
    # reliably from every network, which silently suppressed photos on Cloud Run.
    # stream=True means the body is never downloaded.
    for ext in (".jpg", ".jpeg", ".png"):
        url = f"{config.PHOTO_BASE_URL}/{key}{ext}"
        try:
            response = requests.get(url, timeout=10, stream=True, allow_redirects=True)
            response.close()
            if response.status_code == 200:
                return url
            logger.info("no photo at %s (%s)", url, response.status_code)
        except Exception as exc:  # noqa: BLE001 - a photo must never block a pass
            logger.warning("photo probe failed for %s: %s", url, exc)
    return ""


def photo_url(membership_number: str) -> str:
    """V4 signed GCS URL for Google Wallet. Empty when there is no photo.

    NOTE the expiry. Google caches the image after fetching it, but a pass that is
    never patched again could eventually lose the photo once the URL lapses. The
    URL is re-minted on every upsert_object/patch, so any status, name or expiry
    change refreshes it. For a long-lived unchanged pass, re-patch periodically.
    """
    key = _safe(membership_number)
    if not key:
        return ""
    if config.PHOTO_BASE_URL:
        return remote_photo_url(key)
    if not config.PHOTO_BUCKET:
        return ""
    object_key = ensure_rendered(key)
    if not object_key:
        return ""

    from datetime import timedelta

    from google.cloud import storage
    try:
        blob = storage.Client().bucket(config.PHOTO_BUCKET).blob(object_key)
        return blob.generate_signed_url(
            version="v4",
            expiration=timedelta(days=config.PHOTO_URL_TTL_DAYS),
            method="GET",
            credentials=_signing_credentials(),
        )
    except Exception as exc:  # noqa: BLE001 - a photo must never block a pass
        logger.warning("could not sign photo URL for %s: %s", key, exc)
        return ""
