"""Flask app — all endpoints from LLD section 4.

Public:
    POST /cards                      upsert (create + status change), API-key gated
    GET  /add?token=...              email landing: OS detect -> device gate -> pass
    GET  /healthz

Apple PassKit web service (paths mandated by Apple):
    POST   /v1/devices/<dev>/registrations/<ptid>/<serial>
    DELETE /v1/devices/<dev>/registrations/<ptid>/<serial>
    GET    /v1/devices/<dev>/registrations/<ptid>
    GET    /v1/passes/<ptid>/<serial>
    POST   /v1/log

Support:
    POST /support/reset              free a member's device slots
"""

import logging
from datetime import datetime, timezone

from flask import Flask, Response, abort, jsonify, make_response, render_template, request

from . import config, device_limit, email_svc, ingest, pass_apple, pass_google, state

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("wallet")

app = Flask(__name__)


# --- helpers ----------------------------------------------------------------

def _require_api_key() -> None:
    """POST /cards sits behind Cloud API Gateway with an API key (decision 9 —
    no Apigee). Checked here too so the service is safe if exposed directly.
    """
    supplied = request.headers.get("X-API-Key", "")
    try:
        expected = config.get_secret(config.SECRET_API_KEY)
    except Exception as exc:  # noqa: BLE001
        logger.error("cannot read API key secret: %s", exc)
        abort(500, "API key not configured")
    if not supplied or supplied != expected:
        abort(401, "invalid or missing X-API-Key")


def _require_apple_auth(member: dict) -> None:
    """Apple sends `Authorization: ApplePass <token>` on every web-service call."""
    header = request.headers.get("Authorization", "")
    if not header.startswith("ApplePass "):
        abort(401)
    if header.split(" ", 1)[1].strip() != pass_apple.auth_token_for(member):
        abort(401)


def _issue_and_notify(record: dict) -> dict:
    """Core upsert shared by POST /cards and the CSV job (LLD flow 1)."""
    member = state.upsert_member(
        record[ingest.FIELD_EMAIL],
        record[ingest.FIELD_MEMBERSHIP],
        record[ingest.FIELD_STATUS],
        member_names=record.get(ingest.FIELD_NAMES) or [],
        expiry_date=record.get(ingest.FIELD_EXPIRY, ""),
        member_since=record.get(ingest.FIELD_SINCE, ""),
    )
    outcome = {"email": member["email"], "status": member["status"],
               "new": member["_is_new"], "status_changed": member["_status_changed"]}

    if member["_is_new"]:
        try:
            delivery = email_svc.send_card_email(member)
            state.log_event(member["member_id"], state.EV_EMAIL_SENT, delivery_status=delivery)
            outcome["email_sent"] = True
        except Exception as exc:  # noqa: BLE001 - one bad address must not fail the batch
            logger.error("email failed for %s: %s", member["email"], exc)
            state.log_event(member["member_id"], state.EV_EMAIL_SENT, delivery_status=f"FAILED: {exc}")
            outcome["email_sent"] = False
            outcome["error"] = str(exc)
        return outcome

    if member["_status_changed"]:
        outcome.update(push_update(member))
    return outcome


def push_update(member: dict) -> dict:
    """LLD flow 4 — propagate a status change to existing passes."""
    result = {"apple_pushed": 0, "google_patched": False}

    tokens = state.apple_push_tokens(member["member_id"])
    if tokens:
        try:
            sent, _ = pass_apple.push_updates(tokens)
            result["apple_pushed"] = sent
        except Exception as exc:  # noqa: BLE001
            logger.error("APNs push failed for %s: %s", member["email"], exc)

    if any(d.get("platform") == device_limit.PLATFORM_GOOGLE
           for d in state.active_devices(member["member_id"])):
        try:
            pass_google.upsert_object(member)
            result["google_patched"] = True
        except Exception as exc:  # noqa: BLE001
            logger.error("Google object patch failed for %s: %s", member["email"], exc)

    state.log_event(member["member_id"], state.EV_UPDATE_PUSHED,
                    delivery_status=f"apple={result['apple_pushed']} google={result['google_patched']}")
    return result


# --- health -----------------------------------------------------------------

@app.get("/healthz")
def healthz():
    return jsonify(status="ok", ts=datetime.now(timezone.utc).isoformat())


@app.get("/assets/<path:filename>")
def assets(filename):
    """Serve the card artwork. Google Wallet fetches the logo over HTTPS from here,
    so this must stay publicly reachable. Long cache: assets only change when
    tools/make_assets.py is re-run and the service redeployed.
    """
    from flask import send_from_directory
    import os as _os
    directory = _os.path.join(_os.path.dirname(__file__), "assets")
    response = send_from_directory(directory, filename)
    response.headers["Cache-Control"] = "public, max-age=86400"
    return response


# --- POST /cards ------------------------------------------------------------

@app.post("/cards")
def post_cards():
    _require_api_key()
    try:
        records, errors = ingest.parse_json_payload(request.get_json(force=True, silent=False))
    except ingest.ValidationError as exc:
        return jsonify(error=str(exc)), 400

    results = [_issue_and_notify(record) for record in records]
    return jsonify(
        accepted=len(results),
        rejected=len(errors),
        errors=errors,
        results=results,
    ), (200 if results else 400)


# --- GET /add ---------------------------------------------------------------

@app.get("/add")
def add_card():
    """Email landing page. Also the click-tracking event (LLD flow 2)."""
    member = state.get_member_by_token(request.args.get("token", ""))
    if not member:
        return render_template("error.html", org=config.ORG_NAME,
                               message="This link is not valid or has expired."), 404

    mid = member["member_id"]
    platform = device_limit.detect_platform(request.headers.get("User-Agent", ""))

    if platform == device_limit.PLATFORM_APPLE:
        # Apple's own registration callback is the authoritative count, so we do a
        # soft pre-check here and let the callback make the final decision.
        allowed, count, reason = device_limit.check(mid, "")
        if not allowed:
            state.log_event(mid, state.EV_BLOCKED, delivery_status=reason, os_name="iOS")
            return render_template("limit.html", org=config.ORG_NAME,
                                   support=config.SUPPORT_EMAIL,
                                   limit=config.MAX_DEVICES_PER_MEMBER), 403
        state.log_event(mid, state.EV_CLICKED, os_name="iOS")
        try:
            payload = pass_apple.build_pkpass(member)
        except Exception as exc:  # noqa: BLE001
            logger.exception("pkpass build failed for %s", member["email"])
            return render_template("error.html", org=config.ORG_NAME,
                                   message="We could not build your card. Please contact support."), 500
        response = make_response(payload)
        response.headers["Content-Type"] = "application/vnd.apple.pkpass"
        response.headers["Content-Disposition"] = f'attachment; filename="{mid}.pkpass"'
        return response

    # Google: cookie is the only device signal available (best-effort by design).
    device_id, is_new_cookie = device_limit.google_device_id(request.cookies.get(device_limit.DEVICE_COOKIE))
    allowed, count, reason = device_limit.check(mid, device_id)
    if not allowed:
        state.log_event(mid, state.EV_BLOCKED, delivery_status=reason,
                        device_id=device_id, os_name="Android")
        return render_template("limit.html", org=config.ORG_NAME,
                               support=config.SUPPORT_EMAIL,
                               limit=config.MAX_DEVICES_PER_MEMBER), 403

    try:
        pass_google.upsert_object(member)
        link = pass_google.save_link(member)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Google pass failed for %s", member["email"])
        return render_template("error.html", org=config.ORG_NAME,
                               message="We could not build your card. Please contact support."), 500

    state.register_device(mid, device_id, device_limit.PLATFORM_GOOGLE, os_name="Android")
    state.log_event(mid, state.EV_ADDED, device_id=device_id, os_name="Android")

    response = make_response(render_template(
        "added.html", org=config.ORG_NAME, save_link=link,
        membership=member.get("membership_number", ""),
        status=(member.get("status") or "").upper(),
    ))
    if is_new_cookie:
        response.set_cookie(device_limit.DEVICE_COOKIE, device_id,
                            max_age=device_limit.COOKIE_MAX_AGE,
                            secure=True, httponly=True, samesite="Lax")
    return response


# --- Apple PassKit web service ---------------------------------------------

@app.post("/v1/devices/<device_lib_id>/registrations/<pass_type_id>/<serial>")
def apple_register(device_lib_id, pass_type_id, serial):
    """Authoritative device gate (LLD flow 3). The 3rd device is refused here."""
    member = state.get_member_by_apple_serial(serial)
    if not member:
        abort(404)
    _require_apple_auth(member)

    mid = member["member_id"]
    device_id = device_limit.apple_device_id(device_lib_id)
    push_token = (request.get_json(silent=True) or {}).get("pushToken", "")

    existing = state.find_device(mid, device_id)
    if existing and existing.get("active"):
        state.register_device(mid, device_id, device_limit.PLATFORM_APPLE, os_name="iOS",
                             apple_device_lib_id=device_lib_id, apple_push_token=push_token)
        return "", 200  # already registered

    if state.active_device_count(mid) >= config.MAX_DEVICES_PER_MEMBER:
        state.log_event(mid, state.EV_BLOCKED, delivery_status="limit_reached_apple_registration",
                        device_id=device_id, os_name="iOS")
        logger.info("blocked apple registration for %s (limit %s reached)",
                    member["email"], config.MAX_DEVICES_PER_MEMBER)
        abort(401)  # Apple treats 401 as "do not keep this pass registered"

    state.register_device(mid, device_id, device_limit.PLATFORM_APPLE, os_name="iOS",
                         apple_device_lib_id=device_lib_id, apple_push_token=push_token)
    state.log_event(mid, state.EV_ADDED, device_id=device_id, os_name="iOS")
    return "", 201


@app.delete("/v1/devices/<device_lib_id>/registrations/<pass_type_id>/<serial>")
def apple_unregister(device_lib_id, pass_type_id, serial):
    member = state.get_member_by_apple_serial(serial)
    if not member:
        abort(404)
    _require_apple_auth(member)
    state.deactivate_device(member["member_id"], device_limit.apple_device_id(device_lib_id))
    return "", 200


@app.get("/v1/devices/<device_lib_id>/registrations/<pass_type_id>")
def apple_list_updated(device_lib_id, pass_type_id):
    """Which passes changed since the device last asked."""
    since = request.args.get("passesUpdatedSince", "")
    device_id = device_limit.apple_device_id(device_lib_id)

    serials, latest = [], since
    for member in state.iter_members():
        device = state.find_device(member["member_id"], device_id)
        if not (device and device.get("active")):
            continue
        updated_at = member.get("updated_at")
        stamp = updated_at.isoformat() if hasattr(updated_at, "isoformat") else ""
        if not since or (stamp and stamp > since):
            serials.append(member["apple_serial"])
            latest = max(latest or "", stamp)

    if not serials:
        return "", 204
    return jsonify(serialNumbers=serials, lastUpdated=latest or since)


@app.get("/v1/passes/<pass_type_id>/<serial>")
def apple_get_pass(pass_type_id, serial):
    """Device pulls the latest pass after an APNs nudge."""
    member = state.get_member_by_apple_serial(serial)
    if not member:
        abort(404)
    _require_apple_auth(member)
    payload = pass_apple.build_pkpass(member)
    response = make_response(payload)
    response.headers["Content-Type"] = "application/vnd.apple.pkpass"
    return response


@app.post("/v1/log")
def apple_log():
    for line in (request.get_json(silent=True) or {}).get("logs", []):
        logger.warning("apple pass log: %s", line)
    return "", 200


# --- support reset ----------------------------------------------------------

@app.post("/support/reset")
def support_reset():
    """LLD flow 5. Ownership check is mandatory: the requester must supply the
    registered email, and only that member's slots are freed. Guard with the API
    key so it cannot be called by an end user directly.
    """
    _require_api_key()
    body = request.get_json(silent=True) or {}
    email = str(body.get("email", "")).strip().lower()
    if not email:
        return jsonify(error="email is required"), 400

    member = state.get_member(state.member_id(email))
    if not member:
        return jsonify(error="member not found"), 404

    freed = state.reset_devices(member["member_id"])
    logger.info("support reset for %s: %s device slot(s) freed", email, freed)
    return jsonify(email=email, slots_freed=freed)


if __name__ == "__main__":
    import os
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
