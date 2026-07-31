# Digital Membership Wallet Card Microservice

Python + Flask on Cloud Run. Implements `docs/LLD-Digital-Wallet-Microservice.md`.

## Layout

| Path | Role |
|---|---|
| `app/config.py` | env + Secret Manager access (cached per instance) |
| `app/state.py` | Firestore repository — the only module touching Firestore |
| `app/ingest.py` | validation shared by CSV and JSON entry points |
| `app/device_limit.py` | the 2-device gate |
| `app/pass_apple.py` | `.pkpass` build, PKCS#7 signing, APNs push |
| `app/pass_google.py` | Wallet class/object + save JWT |
| `app/email_svc.py` | Gmail send |
| `app/main.py` | Flask routes incl. the 5 PassKit paths |
| `jobs/csv_job.py` | Cloud Run Job for the CSV batch |
| `tests/test_core.py` | unit tests, no GCP credentials needed |

## Local

```bash
pip install -r requirements.txt
python -m pytest tests -q                     # 10 tests, no cloud access
python -m jobs.csv_job ./members.csv --dry-run # validate a CSV offline
```

`state.py` imports Firestore lazily, so the pure logic runs without GCP libraries.

## Secrets to create

```bash
for s in apple-pass-cert-pem apple-pass-key-pem apple-wwdr-pem \
         apple-apns-authkey-p8 google-wallet-sa-json cards-api-key; do
  gcloud secrets create $s --replication-policy=automatic
done
# then add a version to each, e.g.
gcloud secrets versions add apple-pass-cert-pem --data-file=pass-cert.pem
```

Grant the Cloud Run service account `roles/secretmanager.secretAccessor` and
`roles/datastore.user`.

## Deploy

```bash
gcloud run deploy wallet-service \
  --source . --region australia-southeast1 --allow-unauthenticated \
  --set-env-vars "$(grep -v '^#' .env | xargs | tr ' ' ',')"
```

`--allow-unauthenticated` is required: Apple's servers call the `/v1/*` endpoints
and members open `/add` from an email. Those paths carry their own auth (ApplePass
token, unguessable link token). `POST /cards` and `POST /support/reset` require
`X-API-Key` and should additionally sit behind Cloud API Gateway.

One-off, after the Google issuer account is approved:

```python
from app import pass_google; pass_google.ensure_class()
```

## Endpoints

| Method | Path | Auth |
|---|---|---|
| POST | `/cards` | `X-API-Key` |
| GET | `/add?token=` | link token |
| POST | `/v1/devices/<dev>/registrations/<ptid>/<serial>` | ApplePass |
| DELETE | `/v1/devices/<dev>/registrations/<ptid>/<serial>` | ApplePass |
| GET | `/v1/devices/<dev>/registrations/<ptid>` | ApplePass |
| GET | `/v1/passes/<ptid>/<serial>` | ApplePass |
| POST | `/v1/log` | none (Apple diagnostics) |
| POST | `/support/reset` | `X-API-Key` |
| GET | `/healthz` | none |

```bash
curl -X POST https://cards.example.org/cards \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '[{"email":"a@b.com","membership_number":"12345","status":"ACTIVE"}]'
```

## Behaviour worth knowing

**Device limit.** Authoritative on Apple — the registration callback supplies a real
`deviceLibraryIdentifier`, and the 3rd device gets a 401 so Wallet drops it. On
Google it is best-effort: there is no per-device callback, so the count keys off a
first-party cookie, which is per-browser. Accepted in the LLD.

**Status colour, not bold text.** Neither wallet supports per-field colour or weight,
so ACTIVE/EXPIRED drives the card background (`backgroundColor` / `hexBackgroundColor`).

**Email tracking.** `SENT` means Gmail accepted the message, not that it avoided a
bounce. `CLICKED` is real, because `/add` is our own endpoint. Opens are not tracked.

**Re-adding cannot be prevented.** Anyone holding the `.pkpass` or save link can
re-add it; what is controlled is generation, plus voiding on Apple.

## Before it can run

- Apple Developer Program → Pass Type ID + certificate + APNs key
- Google Wallet issuer account (approval step) + service account
- Domain with HTTPS for `SERVICE_BASE_URL`
- Gmail sender with domain-wide delegation for `EMAIL_SENDER`

## Not yet built

Real card artwork (`icon.png`, `logo.png` — a 1×1 placeholder ships so passes open),
open-tracking, brand scoping for multi-brand reuse (one pass class per brand).
