"""Unit tests for the pure logic — no GCP credentials required.

    cd wallet-service && python -m pytest tests -q
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import ingest  # noqa: E402
from app import device_limit  # noqa: E402


# --- ingest -----------------------------------------------------------------

def test_normalise_lowercases_email_and_uppercases_status():
    out = ingest.normalise_record(
        {"Email Address": " Bob@Example.COM ", "membership": " 12345 ", "status": "active"}
    )
    assert out == {"email": "bob@example.com", "membership_number": "12345", "status": "ACTIVE"}


def test_rejects_bad_email():
    for bad in ("", "nope", "a@b", "a b@c.com"):
        try:
            ingest.normalise_record({"email": bad, "membership": "1", "status": "ACTIVE"})
            assert False, f"should have rejected {bad!r}"
        except ingest.ValidationError:
            pass


def test_rejects_unknown_status():
    try:
        ingest.normalise_record({"email": "a@b.com", "membership": "1", "status": "LAPSED"})
        assert False, "should have rejected LAPSED"
    except ingest.ValidationError as exc:
        assert "status must be one of" in str(exc)


def test_csv_parses_and_dedupes_last_wins():
    csv_text = (
        "email,membership_number,status\n"
        "a@b.com,1,ACTIVE\n"
        "bad-email,2,ACTIVE\n"
        "a@b.com,1,EXPIRED\n"          # supersedes the first row
    )
    records, errors = ingest.parse_csv(csv_text)
    assert len(records) == 1
    assert records[0]["status"] == "EXPIRED"
    assert len(errors) == 1


def test_json_accepts_single_object_and_array():
    single, _ = ingest.parse_json_payload({"email": "a@b.com", "membership": "1", "status": "ACTIVE"})
    array, _ = ingest.parse_json_payload([
        {"email": "a@b.com", "membership": "1", "status": "ACTIVE"},
        {"email": "c@d.com", "membership": "2", "status": "EXPIRED"},
    ])
    assert len(single) == 1 and len(array) == 2


# --- platform detection -----------------------------------------------------

def test_detect_platform():
    ios = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15",
           "Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X)")
    android = ("Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36",)
    for ua in ios:
        assert device_limit.detect_platform(ua) == device_limit.PLATFORM_APPLE
    for ua in android:
        assert device_limit.detect_platform(ua) == device_limit.PLATFORM_GOOGLE
    # unknown falls back to Google, whose save link works in any browser
    assert device_limit.detect_platform("") == device_limit.PLATFORM_GOOGLE


def test_google_device_id_reuses_cookie():
    reused, is_new = device_limit.google_device_id("existing-id")
    assert reused == "existing-id" and is_new is False
    minted, is_new = device_limit.google_device_id(None)
    assert minted and is_new is True


def test_apple_device_id_is_stable_and_hashed():
    a = device_limit.apple_device_id("abcdef0123456789")
    b = device_limit.apple_device_id("abcdef0123456789")
    assert a == b and len(a) == 32 and a != "abcdef0123456789"


# --- pass content -----------------------------------------------------------

def test_pass_json_uses_colour_for_status_not_bold_text():
    from app import config, pass_apple

    config.APPLE_PASS_TYPE_ID = "pass.test"
    config.APPLE_TEAM_ID = "TEAM123"
    member = {"member_id": "m1", "apple_serial": "m1", "email": "a@b.com",
              "membership_number": "12345", "status": "ACTIVE", "link_token": "tok"}

    active = pass_apple.build_pass_json(member, "https://x.example", "auth")
    assert active["backgroundColor"] == config.STATUS_COLOURS["ACTIVE"]["background"]
    assert active["generic"]["secondaryFields"][0]["value"] == "ACTIVE"
    assert active["barcodes"][0]["message"] == "12345"
    assert active["webServiceURL"] == "https://x.example/v1"

    expired = pass_apple.build_pass_json({**member, "status": "EXPIRED"}, "https://x.example", "auth")
    assert expired["backgroundColor"] == config.STATUS_COLOURS["EXPIRED"]["background"]
    assert expired["backgroundColor"] != active["backgroundColor"]


def test_auth_token_is_deterministic_and_not_the_link_token():
    from app import pass_apple
    member = {"member_id": "m1", "link_token": "public-token"}
    token = pass_apple.auth_token_for(member)
    assert token == pass_apple.auth_token_for(member)
    assert token != "public-token"
