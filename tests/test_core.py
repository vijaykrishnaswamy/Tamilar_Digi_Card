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
    assert out == {
        "email": "bob@example.com", "membership_number": "12345", "status": "ACTIVE",
        "member_names": [], "expiry_date": "",
    }


def test_normalise_names_and_dates():
    out = ingest.normalise_record({
        "email": "a@b.com", "membership_number": "1000207", "status": "ACTIVE",
        "member_names": "Vijayakumar Krishnaswamy; Saranya Subramani",
        "expiry_date": "10/10/2026",
    })
    assert out["member_names"] == ["Vijayakumar Krishnaswamy", "Saranya Subramani"]
    assert out["expiry_date"] == "2026-10-10"      # dd/mm/yyyy -> ISO

    # JSON callers can send a real list
    out2 = ingest.normalise_record({
        "email": "a@b.com", "membership_number": "1", "status": "ACTIVE",
        "member_names": ["One Person", "Two Person"],
    })
    assert out2["member_names"] == ["One Person", "Two Person"]


def test_bad_expiry_date_is_rejected_not_dropped():
    try:
        ingest.normalise_record({"email": "a@b.com", "membership_number": "1",
                                 "status": "ACTIVE", "expiry_date": "next tuesday"})
        assert False, "should have rejected an unparseable date"
    except ingest.ValidationError as exc:
        assert "expiry_date" in str(exc)


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

def _sample_member():
    return {
        "member_id": "m1", "apple_serial": "m1", "email": "a@b.com",
        "membership_number": "1000207", "status": "ACTIVE", "link_token": "tok",
        "member_names": ["Vijayakumar Krishnaswamy", "Saranya Subramani"],
        "expiry_date": "2026-10-10",
    }


def test_apple_pass_layout_matches_mockup():
    from app import config, pass_apple

    config.APPLE_PASS_TYPE_ID = "pass.test"
    config.APPLE_TEAM_ID = "TEAM123"
    body = pass_apple.build_pass_json(_sample_member(), "https://x.example", "auth")
    generic = body["generic"]

    # dark slate card, green labels, white values
    assert body["backgroundColor"] == "rgb(57,62,70)"
    assert body["labelColor"] == "rgb(139,195,74)"
    assert body["foregroundColor"] == "rgb(255,255,255)"

    # expiry in the header, right-aligned
    assert generic["headerFields"][0]["label"] == "EXPIRY DATE"
    assert generic["headerFields"][0]["value"] == "10-Oct-2026"

    # both member names on the primary field, one per line
    assert generic["primaryFields"][0]["label"] == "MEMBER NAME"
    assert generic["primaryFields"][0]["value"] == \
        "Vijayakumar Krishnaswamy\nSaranya Subramani"

    # one field per row on the face: MEMBER SINCE lives on the back instead
    labels = [f["label"] for f in generic["secondaryFields"]]
    assert labels == ["MEMBERSHIP NUMBER"]
    assert generic["secondaryFields"][0]["value"] == "1000207"

    assert generic["auxiliaryFields"][0]["label"] == "MEMBERSHIP STATUS"
    assert generic["auxiliaryFields"][0]["value"] == "ACTIVE"

    back = {f["label"]: f["value"] for f in generic["backFields"]}
    assert "Member since" not in back          # dropped entirely
    assert back["Registered email"] == "a@b.com"

    assert body["expirationDate"] == "2026-10-10T23:59:59Z"
    assert body["barcodes"][0]["message"] == "1000207"
    assert body["webServiceURL"] == "https://x.example/v1"


def test_single_name_and_missing_optional_fields():
    from app import pass_apple
    member = {**_sample_member(), "member_names": ["Solo Member"],
              "expiry_date": ""}
    generic = pass_apple.build_pass_json(member, "https://x.example", "auth")["generic"]
    assert generic["primaryFields"][0]["value"] == "Solo Member"
    assert generic["headerFields"] == []                      # no expiry -> no header row
    assert [f["label"] for f in generic["secondaryFields"]] == ["MEMBERSHIP NUMBER"]


def test_google_object_mirrors_apple():
    from app import config, pass_google
    config.GOOGLE_ISSUER_ID = "3388000000012345678"
    body = pass_google._object_body(_sample_member())

    assert body["hexBackgroundColor"] == "#393E46"
    assert body["header"]["defaultValue"]["value"] == \
        "Vijayakumar Krishnaswamy, Saranya Subramani"
    assert body["subheader"]["defaultValue"]["value"] == "Expires on 10-Oct-2026"
    headers = [m["header"] for m in body["textModulesData"]]
    assert headers == ["MEMBERSHIP NUMBER", "MEMBERSHIP STATUS"]
    assert body["validTimeInterval"]["end"]["date"] == "2026-10-10T23:59:59.000Z"
    # No barcode on the Google pass - nothing scans these cards.
    assert "barcode" not in body


def test_apple_assets_are_bundled():
    from app import pass_apple
    images = pass_apple.bundled_images()
    assert "icon.png" in images
    for name in ("logo.png", "logo@2x.png"):
        assert name in images, f"{name} missing - run tools/make_assets.py"
    assert images["logo.png"].startswith(b"\x89PNG")


def test_auth_token_is_deterministic_and_not_the_link_token():
    from app import pass_apple
    member = {"member_id": "m1", "link_token": "public-token"}
    token = pass_apple.auth_token_for(member)
    assert token == pass_apple.auth_token_for(member)
    assert token != "public-token"


# --- lifetime memberships ("NA" expiry) --------------------------------------

def test_ingest_normalises_lifetime_variants():
    for raw in ("NA", "na", "N/A", "n.a.", "LIFETIME", "lifetime", "Nil", "-",
                "PERPETUAL"):
        out = ingest.normalise_record({"email": "a@b.com", "membership_number": "1",
                                       "status": "ACTIVE", "expiry_date": raw})
        assert out["expiry_date"] == "LIFETIME", f"{raw!r} should map to LIFETIME"


def test_apple_lifetime_shows_lifetime_and_omits_expirationDate():
    from app import pass_apple
    member = {**_sample_member(), "expiry_date": "LIFETIME"}
    body = pass_apple.build_pass_json(member, "https://x.example", "auth")

    # face still shows the label, with LIFETIME as the value
    assert body["generic"]["headerFields"][0]["label"] == "EXPIRY DATE"
    assert body["generic"]["headerFields"][0]["value"] == "LIFETIME"
    # critical: no native expiry, or Wallet would expire a lifetime card
    assert "expirationDate" not in body

    dated = pass_apple.build_pass_json(_sample_member(), "https://x.example", "auth")
    assert dated["expirationDate"] == "2026-10-10T23:59:59Z"


def test_google_lifetime_omits_validTimeInterval():
    from app import config, pass_google
    config.GOOGLE_ISSUER_ID = "3388000000012345678"

    body = pass_google._object_body({**_sample_member(), "expiry_date": "LIFETIME"})
    assert "validTimeInterval" not in body
    assert "subheader" not in body               # "Expires LIFETIME" reads badly
    headers = [m["header"] for m in body["textModulesData"]]
    assert headers == ["MEMBERSHIP NUMBER", "MEMBERSHIP STATUS", "EXPIRY DATE"]
    assert body["textModulesData"][-1]["body"] == "LIFETIME"

    dated = pass_google._object_body(_sample_member())
    assert dated["validTimeInterval"]["end"]["date"] == "2026-10-10T23:59:59.000Z"
    assert dated["subheader"]["defaultValue"]["value"] == "Expires on 10-Oct-2026"


def test_legacy_na_still_treated_as_lifetime():
    """Members stored as 'NA' before the label change must not regress."""
    from app import pass_apple, pass_google
    for sentinel in ("NA", "N/A", "LIFETIME"):
        assert pass_apple.is_lifetime(sentinel), sentinel
        assert pass_google.is_lifetime(sentinel), sentinel
        body = pass_apple.build_pass_json({**_sample_member(), "expiry_date": sentinel},
                                         "https://x.example", "auth")
        assert "expirationDate" not in body, sentinel
    assert not pass_apple.is_lifetime("2026-10-10")
    assert not pass_apple.is_lifetime("")


def test_google_expired_uses_past_tense():
    """An EXPIRED card must read "Expired on" - "Expires on" implies it is still valid."""
    from app import config, pass_google
    config.GOOGLE_ISSUER_ID = "3388000000012345678"

    member = _sample_member()
    member["status"] = "EXPIRED"
    body = pass_google._object_body(member)
    assert body["subheader"]["defaultValue"]["value"] == "Expired on 10-Oct-2026"

    member["status"] = "ACTIVE"
    active = pass_google._object_body(member)
    assert active["subheader"]["defaultValue"]["value"] == "Expires on 10-Oct-2026"
