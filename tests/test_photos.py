"""Member photo tests - local dir only, no GCS needed."""

import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image  # noqa: E402

from app import config, photos  # noqa: E402


def _write_jpeg(directory: Path, name: str, size=(1200, 800)) -> Path:
    path = directory / name
    Image.new("RGB", size, (120, 40, 200)).save(path, "JPEG")
    return path


def _reset(tmp_path):
    """Point the loader at a temp dir and clear the per-instance cache."""
    config.PHOTO_LOCAL_DIR = str(tmp_path)
    config.PHOTO_BUCKET = ""
    # Cleared so the suite does not reach out to a public host, or change result
    # depending on whether the developer has PHOTO_BASE_URL exported.
    config.PHOTO_BASE_URL = ""
    config.SERVICE_BASE_URL = "https://wallet.example.org"
    photos.source_bytes.cache_clear()
    photos.remote_photo_url.cache_clear()


def test_no_photo_returns_nothing(tmp_path):
    _reset(tmp_path)
    assert photos.has_photo("1000207") is False
    assert photos.apple_thumbnails("1000207") == {}
    assert photos.google_square("1000207") is None
    assert photos.photo_url("1000207") == ""


def test_photo_url_requires_a_bucket(tmp_path):
    """Signed URLs come from GCS, so with no bucket configured there is no URL even
    when a local photo exists. Apple is unaffected - it embeds the bytes."""
    _reset(tmp_path)
    _write_jpeg(tmp_path, "1000207.jpeg")
    assert photos.has_photo("1000207") is True
    assert photos.apple_thumbnails("1000207") != {}
    assert photos.photo_url("1000207") == ""          # no PHOTO_BUCKET set


def test_rendered_key_layout():
    config.PHOTO_PREFIX = "member-photos/"
    assert photos.rendered_key("1000207") == "member-photos/rendered/1000207.png"


def test_photo_found_by_membership_number(tmp_path):
    _reset(tmp_path)
    _write_jpeg(tmp_path, "1000207.jpeg")
    assert photos.has_photo("1000207") is True

    thumbs = photos.apple_thumbnails("1000207")
    assert set(thumbs) == {"thumbnail.png", "thumbnail@2x.png", "thumbnail@3x.png"}
    for name, expected in (("thumbnail.png", 90), ("thumbnail@2x.png", 180),
                           ("thumbnail@3x.png", 270)):
        image = Image.open(io.BytesIO(thumbs[name]))
        assert image.size == (expected, expected), f"{name} wrong size"
        assert thumbs[name].startswith(b"\x89PNG")

    square = photos.google_square("1000207")
    assert Image.open(io.BytesIO(square)).size == (400, 400)


def test_accepts_jpg_and_png_extensions(tmp_path):
    _reset(tmp_path)
    _write_jpeg(tmp_path, "abc123.JPG")
    assert photos.has_photo("abc123") is True


def test_rejects_path_traversal(tmp_path):
    _reset(tmp_path)
    for bad in ("../secret", "a/b", "", "  ", "x" * 100):
        assert photos.has_photo(bad) is False
        assert photos.apple_thumbnails(bad) == {}


def test_apple_pass_includes_thumbnail_only_when_photo_exists(tmp_path):
    _reset(tmp_path)
    from app import pass_apple

    member = {"member_id": "m1", "apple_serial": "m1", "email": "a@b.com",
              "membership_number": "1000207", "status": "ACTIVE", "link_token": "t",
              "member_names": ["A B"], "expiry_date": "2026-10-10"}

    # without a photo, no thumbnail keys are produced
    assert photos.apple_thumbnails(member["membership_number"]) == {}

    _write_jpeg(tmp_path, "1000207.jpeg")
    photos.source_bytes.cache_clear()
    assert "thumbnail.png" in photos.apple_thumbnails(member["membership_number"])


def test_google_object_photo_module(tmp_path, monkeypatch):
    """The photo occupies heroImage - the only front-of-card image slot Google offers -
    and is omitted entirely when the member has no photo, leaving it blank. It coexists
    with the QR, which Google renders separately at the foot of the card."""
    _reset(tmp_path)
    from app import pass_google
    config.GOOGLE_ISSUER_ID = "3388000000012345678"

    member = {"member_id": "m1", "membership_number": "1000207", "status": "ACTIVE",
              "member_names": ["A B"], "expiry_date": "2026-10-10",
              "google_object_id": "3388000000012345678.m1", "email": "a@b.com"}

    # no photo -> no image key at all, and never a placeholder
    no_photo = pass_google._object_body(member)
    assert "heroImage" not in no_photo
    assert "imageModulesData" not in no_photo

    _write_jpeg(tmp_path, "1000207.jpeg")
    photos.source_bytes.cache_clear()
    public = ("https://raw.githubusercontent.com/vijaykrishnaswamy/"
              "Tamilar_Logo/main/1000207.jpeg")
    monkeypatch.setattr(photos, "photo_url", lambda n: public if n == "1000207" else "")

    body = pass_google._object_body(member)
    assert body["heroImage"]["sourceUri"]["uri"] == public
    assert "imageModulesData" not in body       # front of card, not the detail view
    assert body["barcode"]["value"] == "1000207"  # QR and photo coexist


def test_no_public_photo_route_exists():
    """The public /photo/<n>.png endpoint was removed in favour of signed URLs."""
    from app import main
    rules = [str(r) for r in main.app.url_map.iter_rules()]
    assert not any("/photo/" in r for r in rules), rules
