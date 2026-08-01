"""Build a signed .pkpass on this machine - no GCP, no Cloud Run, no Secret Manager.

Purpose: get a real pass onto an iPhone as soon as the Apple certificate exists, so
the card design can be validated before any infrastructure is stood up.

    python tools/build_local_pass.py \
        --csv samples/first_send.csv \
        --cert certs/pass-cert.pem \
        --key  certs/pass-key.pem \
        --wwdr certs/wwdr.pem \
        --team-id ABCDE12345 \
        --pass-type-id pass.au.org.tamilar.membership \
        --photos "C:/Users/vijay.krishnaswamy/Downloads" \
        --out dist

Then AirDrop or email the .pkpass to the phone and open it.

Notes
-----
* The private key must be unencrypted PEM. If yours has a passphrase:
      openssl rsa -in pass-key-enc.pem -out pass-key.pem
* webServiceURL is written but the endpoints will not exist yet, so the pass will
  install and display correctly but will not receive updates. That is expected at
  this stage and does not stop it opening.
* Nothing here is committed: certs/ and dist/ are gitignored.
"""

import argparse
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Build a signed .pkpass locally")
    parser.add_argument("--csv", required=True, help="members CSV (header required)")
    parser.add_argument("--cert", required=True, help="Pass Type ID certificate, PEM")
    parser.add_argument("--key", required=True, help="its private key, unencrypted PEM")
    parser.add_argument("--wwdr", required=True, help="Apple WWDR intermediate, PEM")
    parser.add_argument("--team-id", required=True)
    parser.add_argument("--pass-type-id", required=True)
    parser.add_argument("--photos", default="", help="folder of <membership>.jpeg photos")
    parser.add_argument("--base-url", default="https://wallet.invalid",
                        help="webServiceURL to embed (endpoints need not exist yet)")
    parser.add_argument("--org", default="Tamilar")
    parser.add_argument("--support", default="")
    parser.add_argument("--out", default="dist")
    args = parser.parse_args(argv)

    from app import config, ingest, pass_apple, photos

    for label, path in (("cert", args.cert), ("key", args.key), ("wwdr", args.wwdr)):
        if not Path(path).exists():
            print(f"missing --{label}: {path}")
            return 2

    # Point config at local files and bypass Secret Manager entirely.
    config.APPLE_TEAM_ID = args.team_id
    config.APPLE_PASS_TYPE_ID = args.pass_type_id
    config.SERVICE_BASE_URL = args.base_url.rstrip("/")
    config.ORG_NAME = args.org
    config.SUPPORT_EMAIL = args.support
    config.PHOTO_LOCAL_DIR = args.photos
    config.PHOTO_BUCKET = ""
    photos.source_bytes.cache_clear()

    local = {
        config.SECRET_APPLE_CERT: Path(args.cert).read_text(encoding="utf-8"),
        config.SECRET_APPLE_KEY: Path(args.key).read_text(encoding="utf-8"),
        config.SECRET_APPLE_WWDR: Path(args.wwdr).read_text(encoding="utf-8"),
    }
    config.get_secret.cache_clear()
    config.get_secret = lambda name, version="latest": local[name]          # noqa: E731
    config.get_secret_bytes = lambda name, version="latest": local[name].encode()  # noqa: E731

    records, errors = ingest.parse_csv(io.open(args.csv, encoding="utf-8-sig").read())
    print(f"CSV {args.csv}: {len(records)} valid, {len(errors)} rejected")
    for e in errors:
        print(f"  REJECT {e}")
    if not records:
        return 1

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    built = 0
    for rec in records:
        member = {
            **rec,
            "member_id": rec["membership_number"],
            "apple_serial": rec["membership_number"],
            "link_token": f"local-{rec['membership_number']}",
        }
        has_photo = photos.has_photo(rec["membership_number"])
        try:
            payload = pass_apple.build_pkpass(member)
        except Exception as exc:  # noqa: BLE001
            print(f"  FAILED {rec['membership_number']}: {type(exc).__name__}: {exc}")
            continue

        target = out_dir / f"{rec['membership_number']}.pkpass"
        target.write_bytes(payload)
        built += 1
        print(f"  built {target}  {len(payload):,} bytes  photo={has_photo}")

    print(f"\n{built} pass(es) written to {out_dir.resolve()}")
    if built:
        print("Email or AirDrop the .pkpass to the iPhone and tap to open.")
    return 0 if built else 1


if __name__ == "__main__":
    sys.exit(main())
