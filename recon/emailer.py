import base64
import os

import httpx
import msal

from .config import Settings

CONTENT_TYPES = {
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pdf": "application/pdf",
}


def send_email(settings: Settings, subject: str, body: str, attachment_paths: list[str]) -> str:
    missing = [n for n, v in [("GRAPH_TENANT_ID", settings.tenant_id), ("GRAPH_CLIENT_ID", settings.client_id),
                              ("GRAPH_CLIENT_SECRET", settings.client_secret), ("GRAPH_SENDER", settings.sender),
                              ("EMAIL_TO", settings.email_to)] if not v]
    if missing:
        raise RuntimeError(f"Missing email settings: {', '.join(missing)}")
    app = msal.ConfidentialClientApplication(
        settings.client_id, authority=f"https://login.microsoftonline.com/{settings.tenant_id}",
        client_credential=settings.client_secret)
    tok = app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])
    if "access_token" not in tok:
        raise RuntimeError(f"Graph auth failed: {tok.get('error_description', tok)}")
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
    r = httpx.post(f"https://graph.microsoft.com/v1.0/users/{settings.sender}/sendMail",
                   headers={"Authorization": "Bearer " + tok["access_token"]}, json=payload, timeout=60)
    r.raise_for_status()
    return f"Email sent to {', '.join(settings.email_to)}"
