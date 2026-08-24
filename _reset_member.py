"""Delete a member document so the next run treats them as new and re-sends the invite.

_issue_and_notify() upserts the member BEFORE sending the email, so a failed send
leaves the member recorded and _is_new False - a retry then silently sends nothing.
Deleting the document is the only way to get the invite re-issued.

REFUSES if the member has any device recorded, active or not. A member who has
already installed the card should not be reset without a deliberate decision,
because their pass would be orphaned from the member record.

Usage:
    python _reset_member.py <email> [--force]
"""
import hashlib
import sys

from google.cloud import firestore

if len(sys.argv) < 2:
    print(__doc__)
    raise SystemExit(2)

email = sys.argv[1].strip().lower()
force = "--force" in sys.argv

client = firestore.Client(project="tamilar-wallet-au-prod")
mid = hashlib.sha256(email.encode()).hexdigest()[:32]
ref = client.collection("members").document(mid)
snap = ref.get()

if not snap.exists:
    print(f"{email}: no member document - already treated as new, nothing to do")
    raise SystemExit(0)

doc = snap.to_dict()
devices = list(ref.collection("devices").stream())
print(f"{email}")
print(f"  member_id  : {mid}")
print(f"  membership : {doc.get('membership_number')}")
print(f"  devices    : {len(devices)}")

if devices and not force:
    print("  REFUSING: devices recorded. Their installed card would be orphaned.")
    print("  Re-run with --force only if that is intended.")
    raise SystemExit(1)

for device in devices:
    device.reference.delete()
ref.delete()
print(f"  DELETED. Next run will treat {email} as new and send the invite.")
