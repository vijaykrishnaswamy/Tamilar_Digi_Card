"""Gmail send (LLD module `email`, decision 4 — Gmail, delivered + clicked).

Tracking reality, stated plainly:
  * SENT    - the Gmail API accepted the message. This is what we record.
  * CLICKED - free, because the /add link points at this service.
  * DELIVERED (not bounced) - Gmail API does NOT confirm this. "Delivered" here
    means "sent successfully", per the accepted constraint in LLD section 8.
  * OPENED  - not implemented. Would need a tracking pixel or an ESP.

Auth uses a service account with domain-wide delegation, impersonating
EMAIL_SENDER. Without delegation configured this raises and the caller logs it.
"""

import base64
import logging
from email.message import EmailMessage
from typing import Dict, Optional

from . import config

logger = logging.getLogger(__name__)

GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.send"
_service = None


def _gmail():
    global _service
    if _service is None:
        import json
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        config.require("EMAIL_SENDER")
        info = json.loads(config.get_secret(config.SECRET_GOOGLE_SA))
        creds = service_account.Credentials.from_service_account_info(
            info, scopes=[GMAIL_SCOPE], subject=config.EMAIL_SENDER
        )
        _service = build("gmail", "v1", credentials=creds, cache_discovery=False)
    return _service


def add_card_url(member: Dict) -> str:
    return f"{config.SERVICE_BASE_URL}/add?token={member['link_token']}"


def _body_html(member: Dict, url: str) -> str:
    status = (member.get("status") or "").upper()
    colour = config.STATUS_COLOURS.get(status, config.STATUS_COLOURS["EXPIRED"])["hex"]
    return f"""\
<html><body style="font-family:Arial,Helvetica,sans-serif;color:#111">
  <p>Hello,</p>
  <p>Your {config.ORG_NAME} membership card is ready to add to your phone.</p>
  <table style="border-collapse:collapse;margin:16px 0">
    <tr><td style="padding:4px 12px 4px 0;color:#555">Membership Number</td>
        <td style="padding:4px 0"><strong>{member.get('membership_number','')}</strong></td></tr>
    <tr><td style="padding:4px 12px 4px 0;color:#555">Status</td>
        <td style="padding:4px 0"><strong style="color:{colour}">{status}</strong></td></tr>
  </table>
  <p><a href="{url}"
        style="background:#111;color:#fff;padding:12px 20px;border-radius:6px;
               text-decoration:none;display:inline-block">Add to my wallet</a></p>
  <p style="color:#555;font-size:13px">
    Works on iPhone (Apple Wallet) and Android (Google Wallet). This link is
    personal to you, so please do not forward it. You can add the card to at most
    {config.MAX_DEVICES_PER_MEMBER} devices. Need help? Contact
    {config.SUPPORT_EMAIL or 'your administrator'}.
  </p>
</body></html>"""


def send_card_email(member: Dict) -> str:
    """Send the invite. Returns a delivery status string for the event log."""
    config.require("SERVICE_BASE_URL")
    url = add_card_url(member)

    message = EmailMessage()
    message["To"] = member["email"]
    message["From"] = config.EMAIL_SENDER
    message["Subject"] = f"Your {config.ORG_NAME} membership card"
    message.set_content(
        f"Your {config.ORG_NAME} membership card is ready.\n\n"
        f"Membership Number: {member.get('membership_number','')}\n"
        f"Status: {(member.get('status') or '').upper()}\n\n"
        f"Add it to your phone: {url}\n\n"
        f"This link is personal to you. Limit {config.MAX_DEVICES_PER_MEMBER} devices.\n"
    )
    message.add_alternative(_body_html(member, url), subtype="html")

    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")
    result = _gmail().users().messages().send(userId="me", body={"raw": raw}).execute()
    logger.info("email sent to %s (gmail id=%s)", member["email"], result.get("id"))
    return "SENT"
