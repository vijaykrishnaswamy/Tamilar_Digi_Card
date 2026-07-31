"""Input validation and normalisation for both entry points (LLD module `ingest`).

Same rules for the CSV job and POST /cards, so a row can never be accepted by one
path and rejected by the other.
"""

import csv
import io
import logging
import re
from typing import Any, Dict, List, Tuple

from . import config

logger = logging.getLogger(__name__)

# Deliberately permissive: this rejects obvious junk without pretending to be a
# full RFC 5322 validator. Real delivery failures surface as email bounces.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")

FIELD_EMAIL = "email"
FIELD_MEMBERSHIP = "membership_number"
FIELD_STATUS = "status"

# Accepted CSV header aliases -> canonical field
_ALIASES = {
    "email": FIELD_EMAIL,
    "email_address": FIELD_EMAIL,
    "emailaddress": FIELD_EMAIL,
    "membership_number": FIELD_MEMBERSHIP,
    "membershipnumber": FIELD_MEMBERSHIP,
    "membership": FIELD_MEMBERSHIP,
    "member_number": FIELD_MEMBERSHIP,
    "status": FIELD_STATUS,
    "membership_status": FIELD_STATUS,
}


class ValidationError(ValueError):
    pass


def _canonical_key(key: str) -> str:
    return _ALIASES.get((key or "").strip().lower().replace(" ", "_"), (key or "").strip().lower())


def normalise_record(raw: Dict[str, Any]) -> Dict[str, str]:
    """Validate one record. Raises ValidationError with a usable message."""
    record = {_canonical_key(k): (v if v is not None else "") for k, v in (raw or {}).items()}

    email = str(record.get(FIELD_EMAIL, "")).strip().lower()
    membership = str(record.get(FIELD_MEMBERSHIP, "")).strip()
    status = str(record.get(FIELD_STATUS, "")).strip().upper()

    if not email:
        raise ValidationError("email is required")
    if not _EMAIL_RE.match(email):
        raise ValidationError(f"invalid email: {email}")
    if not membership:
        raise ValidationError(f"membership_number is required (email={email})")
    if status not in config.VALID_STATUSES:
        raise ValidationError(
            f"status must be one of {config.VALID_STATUSES}, got '{status}' (email={email})"
        )

    return {FIELD_EMAIL: email, FIELD_MEMBERSHIP: membership, FIELD_STATUS: status}


def parse_json_payload(payload: Any) -> Tuple[List[Dict[str, str]], List[str]]:
    """Accepts a single object or an array (LLD 1.2). Returns (valid, errors)."""
    if isinstance(payload, dict):
        # tolerate {"records": [...]} as well as a bare object
        payload = payload.get("records", payload) if "records" in payload else [payload]
    if not isinstance(payload, list):
        raise ValidationError("payload must be a JSON object or array")
    return _collect(payload)


def parse_csv(data: str) -> Tuple[List[Dict[str, str]], List[str]]:
    """Parse the CSV job input (LLD 1.1)."""
    reader = csv.DictReader(io.StringIO(data))
    if not reader.fieldnames:
        raise ValidationError("CSV has no header row")
    return _collect(list(reader))


def _collect(rows: List[Dict[str, Any]]) -> Tuple[List[Dict[str, str]], List[str]]:
    """Validate and de-duplicate. Last occurrence of an email wins, so a corrected
    row later in the same file supersedes an earlier one.
    """
    errors: List[str] = []
    by_email: Dict[str, Dict[str, str]] = {}

    for index, row in enumerate(rows, start=1):
        try:
            record = normalise_record(row)
        except ValidationError as exc:
            errors.append(f"row {index}: {exc}")
            continue
        if record[FIELD_EMAIL] in by_email:
            logger.info("row %s: duplicate email %s - superseding earlier row",
                        index, record[FIELD_EMAIL])
        by_email[record[FIELD_EMAIL]] = record

    return list(by_email.values()), errors
