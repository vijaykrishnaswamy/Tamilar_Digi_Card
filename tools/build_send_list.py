#!/usr/bin/env python3
"""Build the wallet send list from the JoinIt membership export.

Handles both export shapes seen so far:
  v1  comma-separated, header declares 8 columns but rows carry 9 fields, with the
      expiry landing in EITHER the 8th or 9th position
  v2  tab-separated, 8 columns, expiry already populated including the literal
      "Life Time"

Output: emailId,Name,MembershipNumber,Status,StartDate,ExpiryDate
Lifetime members carry the literal "Life Time" as ExpiryDate, never a date. A
far-future date such as 26/08/2099 on a lifetime row is treated as a placeholder
and replaced.

Exclusions live in EXCLUDE_MEMBERSHIP so a row removed by decision stays removed
when the source is re-exported.

Usage:
  python build_send_list.py <input.csv|input.tsv> <output.csv>
"""

import csv
import sys
from collections import Counter

OUT_HEADER = ["emailId", "Name", "MembershipNumber", "Status", "StartDate", "ExpiryDate"]
LIFETIME_LABEL = "Life Time"

# Confirmed duplicate records. 1000277 duplicates 1000229 (same person, same
# lifetime membership); the earlier record is retained.
EXCLUDE_MEMBERSHIP = {"1000277"}


def is_lifetime(value):
    return "life" in (value or "").strip().lower().replace(" ", "")


def main(src, dst):
    with open(src, newline="", encoding="utf-8-sig") as fh:
        sample = fh.read(4096)
        fh.seek(0)
        delimiter = "\t" if "\t" in sample.splitlines()[0] else ","
        rows = list(csv.reader(fh, delimiter=delimiter))

    out, skipped, notes = [], 0, []

    for lineno, raw in enumerate(rows[1:], start=2):
        cells = [c.strip() for c in raw]
        if not any(cells):
            skipped += 1
            continue
        while len(cells) < 9:
            cells.append("")

        email, first, last, mtype, number, status, start = cells[:7]
        # v1 puts expiry in either position; v2 always in the 8th.
        expiry = cells[7] or cells[8]

        if not email or not number:
            skipped += 1
            notes.append(f"line {lineno}: dropped, missing email or membership number")
            continue

        if number in EXCLUDE_MEMBERSHIP:
            skipped += 1
            notes.append(f"line {lineno}: {number} EXCLUDED by decision (duplicate record)")
            continue

        name = " ".join(p for p in (first, last) if p)

        if is_lifetime(mtype):
            if expiry and not is_lifetime(expiry):
                notes.append(f"line {lineno}: {number} lifetime carried date {expiry}; "
                             f"replaced with '{LIFETIME_LABEL}'")
            expiry_out = LIFETIME_LABEL
        else:
            expiry_out = expiry
            if not expiry:
                notes.append(f"line {lineno}: {number} '{mtype}' has NO expiry date")

        out.append([email, name, number, status, start, expiry_out])

    with open(dst, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(OUT_HEADER)
        writer.writerows(out)

    lifetime = sum(1 for r in out if r[5] == LIFETIME_LABEL)
    print(f"written {len(out)} rows to {dst}  (skipped {skipped}, delimiter={delimiter!r})")
    print(f"  lifetime : {lifetime}")
    print(f"  dated    : {len(out) - lifetime}")

    dups = [e for e, n in Counter(r[0] for r in out).items() if n > 1]
    if dups:
        print(f"  duplicate emails ({len(dups)}):")
        for e in dups:
            for r in out:
                if r[0] == e:
                    print(f"    {e}  {r[2]}  {r[1]}")
    for n in notes:
        print("  NOTE", n)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(2)
    main(sys.argv[1], sys.argv[2])
