"""Member photo lookup and sizing (optional per member).

Photos are keyed on the MEMBERSHIP NUMBER, matching how they are named on disk
(e.g. 1000207.jpeg -> membership_number "1000207"). That is deliberately NOT the
internal member_id (a sha256 of the email) - the operator names files from the
membership number they can see.

Storage: a GCS bucket, because Cloud Run cannot read the operator's laptop. Upload
with tools/upload_photos.py. A local directory is also supported for development.

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
        return _read_local(key) or _read_gcs(key)
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


def photo_url(membership_number: str) -> str:
    """Public URL Google Wallet fetches. Empty when the member has no photo."""
    key = _safe(membership_number)
    if not key or not config.SERVICE_BASE_URL or not has_photo(key):
        return ""
    return f"{config.SERVICE_BASE_URL}/photo/{key}.png"
