"""Cloud Run Job — CSV batch entry point (LLD section 4, decision: no queue).

Reads a CSV from GCS (or a local path for testing) and runs the same core upsert
as POST /cards, so a row cannot be treated differently by the two entry points.

At 1,000 initial records and 10-50/day a synchronous loop is correct; Pub/Sub and
async workers would be over-engineering at this volume.

Usage:
    python -m jobs.csv_job gs://bucket/members.csv
    python -m jobs.csv_job ./members.csv --dry-run
"""

import argparse
import logging
import sys

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("csv_job")


def read_source(path: str) -> str:
    if path.startswith("gs://"):
        from google.cloud import storage
        bucket_name, _, blob_name = path[5:].partition("/")
        client = storage.Client()
        return client.bucket(bucket_name).blob(blob_name).download_as_text()
    with open(path, "r", encoding="utf-8-sig") as handle:
        return handle.read()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Load membership cards from CSV")
    parser.add_argument("source", help="gs://bucket/key.csv or a local path")
    parser.add_argument("--dry-run", action="store_true",
                        help="validate only; no Firestore writes, no email")
    parser.add_argument("--limit", type=int, default=0, help="process at most N rows")
    args = parser.parse_args(argv)

    from app import ingest

    try:
        records, errors = ingest.parse_csv(read_source(args.source))
    except ingest.ValidationError as exc:
        logger.error("cannot parse %s: %s", args.source, exc)
        return 2

    if args.limit:
        records = records[: args.limit]

    logger.info("=== CSV load: %s ===", args.source)
    logger.info("valid rows: %s | rejected: %s | dry_run=%s",
                len(records), len(errors), args.dry_run)
    for line in errors[:50]:
        logger.error("  reject: %s", line)
    if len(errors) > 50:
        logger.error("  ... and %s more rejects", len(errors) - 50)

    if args.dry_run:
        logger.info("dry run - nothing written")
        return 0 if not errors else 1

    # imported here so --dry-run needs no GCP credentials
    from app.main import _issue_and_notify

    sent = updated = failed = 0
    for index, record in enumerate(records, start=1):
        try:
            outcome = _issue_and_notify(record)
            if outcome.get("email_sent"):
                sent += 1
            elif outcome.get("status_changed"):
                updated += 1
        except Exception as exc:  # noqa: BLE001 - never let one row kill the batch
            failed += 1
            logger.error("row %s (%s) failed: %s", index, record.get("email"), exc)
        if index % 100 == 0:
            logger.info("  processed %s/%s", index, len(records))

    logger.info("=== COMPLETE: %s processed | %s invited | %s updated | %s failed | %s rejected ===",
                len(records), sent, updated, failed, len(errors))
    return 1 if (failed or errors) else 0


if __name__ == "__main__":
    sys.exit(main())
