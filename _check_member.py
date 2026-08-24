"""Throwaway: is a member already in Firestore (so email-suppressed), and what is stored?"""
import hashlib
import sys

from google.cloud import firestore

client = firestore.Client(project="tamilar-wallet-au-prod")

for email in sys.argv[1:]:
    mid = hashlib.sha256(email.encode()).hexdigest()[:32]
    ref = client.collection("members").document(mid)
    snap = ref.get()
    if not snap.exists:
        print(f"{email}\n  NOT in Firestore -> _is_new=True -> EMAIL WILL BE SENT")
        continue
    doc = snap.to_dict()
    devices = [(d.id, d.to_dict().get("platform"), d.to_dict().get("active"))
               for d in ref.collection("devices").stream()]
    print(f"{email}")
    print(f"  EXISTS -> _is_new=False -> NO EMAIL (silent push_update only)")
    print(f"  membership : {doc.get('membership_number')}")
    print(f"  status     : {doc.get('status')}")
    print(f"  expiry     : {doc.get('expiry_date')}")
    print(f"  names      : {doc.get('member_names')}")
    print(f"  devices    : {devices}")
