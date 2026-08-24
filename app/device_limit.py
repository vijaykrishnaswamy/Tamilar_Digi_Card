"""Device-limit gate (LLD 3.2, flows 2 and 3).

Two enforcement points, deliberately:

  1. GET /add  - gates BOTH platforms before a pass is generated.
  2. Apple PassKit registration - the AUTHORITATIVE count, because Apple hands us
     a real per-device identifier (deviceLibraryIdentifier).

Google Wallet has no per-device registration callback, so its count is a proxy
derived from a first-party cookie. That is per-browser, not per-device: clearing
cookies or switching browser yields a new id. This is a platform limitation, not
a design shortcut, and the business has accepted it as best-effort.

We cannot truly PREVENT an add either — anyone holding the .pkpass or save link can
re-add it. What we control is generation, plus voiding on Apple.
"""

import hashlib
import logging
import secrets
from typing import Optional, Tuple

from . import config, state

logger = logging.getLogger(__name__)

DEVICE_COOKIE = "wsvc_did"
COOKIE_MAX_AGE = 60 * 60 * 24 * 365 * 2  # 2 years

PLATFORM_APPLE = "APPLE"
PLATFORM_GOOGLE = "GOOGLE"
# Not a wallet platform. A desktop browser cannot hold a pass, so a click from one
# must not be treated as an install - see detect_platform.
PLATFORM_DESKTOP = "DESKTOP"

# Mobile Apple devices. 'macintosh' / 'mac os x' are deliberately NOT here: macOS
# is a desktop and is handled as such, so a laptop click does not burn a slot.
_APPLE_MOBILE = ("iphone", "ipad", "ipod")

# Desktop operating systems. Checked AFTER the mobile tokens because iPadOS reports
# a Macintosh UA, and some Android tablets include 'linux'.
_DESKTOP_HINTS = ("windows nt", "macintosh", "mac os x", "cros", "x11", "linux")


def detect_platform(user_agent: str) -> str:
    """Serve-time platform from the User-Agent (LLD flow 2).

    Three outcomes, not two. Returning GOOGLE for an unrecognised UA meant a click
    from a Windows laptop entered the Google branch, minted a device id and consumed
    one of the member's two slots - the member then hit "device limit reached" having
    installed the card on a single phone. A desktop is now classified separately and
    no device is registered for it.

    Only used to choose what to serve. It is NOT a device identity - that comes from
    Apple's registration callback or the cookie.
    """
    ua = (user_agent or "").lower()
    if not ua:
        # No UA at all is far more likely to be a bot or link-scanner than a member.
        return PLATFORM_DESKTOP
    if any(token in ua for token in _APPLE_MOBILE):
        return PLATFORM_APPLE
    if "android" in ua:
        return PLATFORM_GOOGLE
    if any(token in ua for token in _DESKTOP_HINTS):
        return PLATFORM_DESKTOP
    # Unknown and not obviously desktop: treat as Android. The Google save link
    # works in any browser, so this stays the safer fallback for a real handset
    # with an unusual UA.
    return PLATFORM_GOOGLE


def new_device_id() -> str:
    return secrets.token_urlsafe(16)


def google_device_id(cookie_value: Optional[str]) -> Tuple[str, bool]:
    """Return (device_id, is_new). Reuses the cookie so a repeat click from the
    same browser does not burn a second slot.
    """
    if cookie_value:
        return cookie_value, False
    return new_device_id(), True


def apple_device_id(device_library_identifier: str) -> str:
    """Apple's deviceLibraryIdentifier is long; hash it for a stable doc id."""
    return hashlib.sha256(device_library_identifier.encode("utf-8")).hexdigest()[:32]


def check(member_id: str, device_id: str) -> Tuple[bool, int, str]:
    """Decide whether this device may add the pass.

    Returns (allowed, active_count, reason).

    An already-registered device is always allowed — re-adding on a device you
    already used must not be treated as a new install.
    """
    if device_id and state.find_device(member_id, device_id):
        existing = state.find_device(member_id, device_id)
        if existing.get("active"):
            return True, state.active_device_count(member_id), "known_device"

    count = state.active_device_count(member_id)
    if count >= config.MAX_DEVICES_PER_MEMBER:
        return False, count, "limit_reached"
    return True, count, "ok"
