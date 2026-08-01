"""Upload member photos from a local folder to the GCS photo bucket.

Photos must be named by MEMBERSHIP NUMBER, e.g. 1000207.jpeg. Anything that does
not look like a membership number is reported and skipped rather than uploaded to a
key the service will never look for.

    python tools/upload_photos.py "C:/Users/me/photos" --bucket tamilar-member-photos
    python tools/upload_photos.py "C:/Users/me/photos" --bucket b --dry-run
"""

import argparse
import re
import sys
from pathlib import Path

SAFE_KEY = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
EXTENSIONS = {".jpg", ".jpeg", ".png"}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Upload member photos to GCS")
    parser.add_argument("folder", help="local folder containing <membership>.jpeg files")
    parser.add_argument("--bucket", required=True, help="destination GCS bucket")
    parser.add_argument("--prefix", default="member-photos/", help="object key prefix")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    folder = Path(args.folder)
    if not folder.is_dir():
        print(f"not a folder: {folder}")
        return 2

    candidates, skipped = [], []
    for path in sorted(folder.iterdir()):
        if not path.is_file():
            continue
        if path.suffix.lower() not in EXTENSIONS:
            skipped.append((path.name, "not an image"))
            continue
        if not SAFE_KEY.match(path.stem):
            skipped.append((path.name, "filename is not a plain membership number"))
            continue
        candidates.append(path)

    print(f"folder      : {folder}")
    print(f"destination : gs://{args.bucket}/{args.prefix}")
    print(f"to upload   : {len(candidates)}   skipped: {len(skipped)}")
    for name, why in skipped[:20]:
        print(f"  skip  {name:40} {why}")

    if args.dry_run:
        for path in candidates[:20]:
            print(f"  would upload {path.name} -> {args.prefix}{path.name}")
        return 0

    from google.cloud import storage
    bucket = storage.Client().bucket(args.bucket)

    done = 0
    for path in candidates:
        blob = bucket.blob(f"{args.prefix}{path.name}")
        blob.upload_from_filename(str(path))
        done += 1
        if done % 25 == 0:
            print(f"  uploaded {done}/{len(candidates)}")

    print(f"uploaded {done} photo(s)")
    return 1 if skipped else 0


if __name__ == "__main__":
    sys.exit(main())
