"""Microsoft Graph client: sends the compliance report email and polls the mailbox for the
compliance officer's approve/reject reply so the review loop can close itself.

Requires an Entra app registration with application permissions Mail.Send (to send the report)
and Mail.Read (to poll for replies) and admin consent.
"""
import base64
import os
import re

import httpx
import msal

from .config import Settings

CONTENT_TYPES = {
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pdf": "application/pdf",
}
GRAPH = "https://graph.microsoft.com/v1.0"
CASE_TOKEN = re.compile(r"\[Case:\s*([A-Za-z0-9_-]+)\]")


def _token(settings: Settings) -> str:
    missing = [n for n, v in [("GRAPH_TENANT_ID", settings.tenant_id), ("GRAPH_CLIENT_ID", settings.client_id),
                              ("GRAPH_CLIENT_SECRET", settings.client_secret), ("GRAPH_SENDER", settings.sender)]
              if not v]
    if missing:
        raise RuntimeError(f"Missing Graph settings: {', '.join(missing)}")
    app = msal.ConfidentialClientApplication(
        settings.client_id, authority=f"https://login.microsoftonline.com/{settings.tenant_id}",
        client_credential=settings.client_secret)
    tok = app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])
    if "access_token" not in tok:
        raise RuntimeError(f"Graph auth failed: {tok.get('error_description', tok)}")
    return tok["access_token"]


def send_email(settings: Settings, subject: str, body: str, attachment_paths: list[str]) -> str:
    if not settings.email_to:
        raise RuntimeError("Missing email settings: EMAIL_TO")
    access_token = _token(settings)
    attachments = []
    for path in attachment_paths:
        with open(path, "rb") as f:
            content = base64.b64encode(f.read()).decode()
        attachments.append({"@odata.type": "#microsoft.graph.fileAttachment", "name": os.path.basename(path),
                            "contentType": CONTENT_TYPES.get(os.path.splitext(path)[1].lower(), "application/octet-stream"),
                            "contentBytes": content})
    payload = {"message": {
        "subject": subject,
        "body": {"contentType": "Text", "content": body},
        "toRecipients": [{"emailAddress": {"address": a}} for a in settings.email_to],
        "attachments": attachments},
        "saveToSentItems": True}
    r = httpx.post(f"{GRAPH}/users/{settings.sender}/sendMail",
                   headers={"Authorization": "Bearer " + access_token}, json=payload, timeout=60)
    r.raise_for_status()
    return f"Email sent to {', '.join(settings.email_to)}"


def list_unread_replies(settings: Settings) -> list[dict]:
    """Unread inbox messages whose subject carries a `[Case: ...]` token, oldest first.
    Each item: {id, case_id, from_address, subject, body_text, conversation_id}."""
    access_token = _token(settings)
    params = {"$filter": "isRead eq false", "$select": "id,subject,from,bodyPreview,body,conversationId",
             "$orderby": "receivedDateTime asc", "$top": "50"}
    r = httpx.get(f"{GRAPH}/users/{settings.sender}/mailFolders/Inbox/messages",
                  headers={"Authorization": "Bearer " + access_token}, params=params, timeout=30)
    r.raise_for_status()
    out = []
    for msg in r.json().get("value", []):
        m = CASE_TOKEN.search(msg.get("subject") or "")
        if not m:
            continue
        body = msg.get("body", {}).get("content", "") or msg.get("bodyPreview", "")
        out.append({"id": msg["id"], "case_id": m.group(1),
                   "from_address": (msg.get("from") or {}).get("emailAddress", {}).get("address", ""),
                   "subject": msg.get("subject", ""), "body_text": _strip_html(body),
                   "conversation_id": msg.get("conversationId", "")})
    return out


def mark_read(settings: Settings, message_id: str) -> None:
    access_token = _token(settings)
    httpx.patch(f"{GRAPH}/users/{settings.sender}/messages/{message_id}",
               headers={"Authorization": "Bearer " + access_token}, json={"isRead": True}, timeout=30).raise_for_status()


def reply_to(settings: Settings, message_id: str, comment: str) -> None:
    access_token = _token(settings)
    httpx.post(f"{GRAPH}/users/{settings.sender}/messages/{message_id}/reply",
              headers={"Authorization": "Bearer " + access_token}, json={"comment": comment}, timeout=30).raise_for_status()


def _strip_html(html: str) -> str:
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()
