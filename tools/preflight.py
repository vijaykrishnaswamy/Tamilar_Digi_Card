"""Local pre-flight: proves ingest, photo lookup and pass content with NO cloud access.

Run from wallet-service/:
    python tools/preflight.py
"""

import io
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.chdir(Path(__file__).resolve().parents[1])

from app import config, ingest, pass_apple, pass_google, photos  # noqa: E402

DOWNLOADS = str(Path.home() / "Downloads")

config.PHOTO_LOCAL_DIR = DOWNLOADS
config.PHOTO_BUCKET = ""
config.SERVICE_BASE_URL = "https://wallet.tamilar.org.au"
config.APPLE_TEAM_ID = "PENDING"
config.APPLE_PASS_TYPE_ID = "pass.au.org.tamilar.membership"
config.GOOGLE_ISSUER_ID = "PENDING"
config.ORG_NAME = "Tamilar"
config.SUPPORT_EMAIL = "support@tamilar.org.au"
photos.source_bytes.cache_clear()

print("=" * 70)
print("1. CSV VALIDATION")
print("=" * 70)
raw = io.open("samples/first_send.csv", encoding="utf-8-sig").read()
records, errors = ingest.parse_csv(raw)
print(f"  valid: {len(records)}   rejected: {len(errors)}")
for e in errors:
    print(f"  REJECT: {e}")
if not records:
    sys.exit(1)
rec = records[0]
print(f"  parsed -> {json.dumps(rec, ensure_ascii=False)}")

print()
print("=" * 70)
print("2. PHOTO LOOKUP  (Downloads, keyed on membership_number)")
print("=" * 70)
num = rec["membership_number"]
print(f"  looking for {num}.jpeg|.jpg|.png in {DOWNLOADS}")
print(f"  found            : {photos.has_photo(num)}")
thumbs = photos.apple_thumbnails(num)
for name, data in sorted(thumbs.items()):
    print(f"    {name:20} {len(data):>7,} bytes")

print()
print("=" * 70)
print("3. APPLE pass.json")
print("=" * 70)
member = {
    **rec,
    "member_id": "preflight",
    "apple_serial": "preflight",
    "link_token": "preflight-token",
    "google_object_id": "PENDING.preflight",
}
body = pass_apple.build_pass_json(member, config.SERVICE_BASE_URL, "authtoken")
g = body["generic"]
print(f"  backgroundColor  : {body['backgroundColor']}")
print(f"  labelColor       : {body['labelColor']}")
for f in g["headerFields"]:
    print(f"  header   {f['label']:<20} {f['value']}")
for f in g["primaryFields"]:
    print(f"  primary  {f['label']:<20} {f['value']!r}")
for f in g["secondaryFields"]:
    print(f"  second   {f['label']:<20} {f['value']}")
for f in g["auxiliaryFields"]:
    print(f"  aux      {f['label']:<20} {f['value']}")
print(f"  expirationDate   : {body.get('expirationDate', 'OMITTED (lifetime)')}")
print(f"  barcode          : {body['barcodes'][0]['message']}")

print()
print("=" * 70)
print("4. FILES THAT WOULD GO IN THE .pkpass")
print("=" * 70)
files = {"pass.json": json.dumps(body).encode(), **pass_apple.bundled_images(), **thumbs}
for name in sorted(files):
    print(f"    {name:20} {len(files[name]):>7,} bytes")
print(f"  total {len(files)} files, {sum(len(v) for v in files.values()):,} bytes")

print()
print("=" * 70)
print("5. GOOGLE genericObject")
print("=" * 70)
gb = pass_google._object_body(member)
print(f"  hexBackgroundColor : {gb['hexBackgroundColor']}")
print(f"  header             : {gb['header']['defaultValue']['value']}")
print(f"  subheader          : "
      f"{gb.get('subheader', {}).get('defaultValue', {}).get('value', 'OMITTED (lifetime)')}")
for m in gb["textModulesData"]:
    print(f"  module   {m['header']:<20} {m['body']}")
print(f"  validTimeInterval  : "
      f"{gb.get('validTimeInterval', {}).get('end', {}).get('date', 'OMITTED (lifetime)')}")
print(f"  photo module       : {'imageModulesData' in gb} "
      f"(needs PHOTO_BUCKET for the signed URL)")

print()
print("=" * 70)
print("BLOCKED ON")
print("=" * 70)
for label, value in (("APPLE_TEAM_ID", config.APPLE_TEAM_ID),
                     ("APPLE_PASS_TYPE_ID", config.APPLE_PASS_TYPE_ID),
                     ("GOOGLE_ISSUER_ID", config.GOOGLE_ISSUER_ID)):
    state = "SET" if value and value != "PENDING" else "PENDING"
    print(f"  {label:20} {state}")
print("  pass certificate + key   PENDING  (needed to sign a .pkpass)")
print("  APNs .p8 key             PENDING  (needed for updates)")
