"""Salesforce client: pulls the client<->advisor notes and comments tied to a brokerage account number.

Authenticates with a connected app (OAuth2 client-credentials flow) by default; falls back to
username/password + security token if SF_USERNAME/SF_PASSWORD/SF_SECURITY_TOKEN are set instead.
Notes/comments come from four object types, normalised into SalesforceNote:
  - Case            (subject/description of any case opened for the account)
  - CaseComment      (comments threaded under a Case)
  - Task             (logged calls/meetings between advisor and client)
  - EmailMessage     (emails associated with the account's Cases)

The account number from a brokerage statement is matched against a custom field
(default `Account_Number__c`) on the Salesforce Account object; override with SF_ACCOUNT_NUMBER_FIELD
if your org uses a different field.
"""
import os

from simple_salesforce import Salesforce

from .config import Settings
from .models import SalesforceNote

ACCOUNT_NUMBER_FIELD = os.getenv("SF_ACCOUNT_NUMBER_FIELD", "Account_Number__c")

NOTES_SOQL = """
SELECT Id, CreatedDate, CreatedBy.Name, Subject, Description
FROM Case WHERE AccountId = '{account_id}' ORDER BY CreatedDate DESC LIMIT 50
"""
COMMENTS_SOQL = """
SELECT Id, CreatedDate, CreatedBy.Name, CommentBody, ParentId
FROM CaseComment WHERE ParentId IN (SELECT Id FROM Case WHERE AccountId = '{account_id}')
ORDER BY CreatedDate DESC LIMIT 100
"""
TASKS_SOQL = """
SELECT Id, CreatedDate, Owner.Name, Subject, Description
FROM Task WHERE WhatId = '{account_id}' ORDER BY CreatedDate DESC LIMIT 50
"""
EMAILS_SOQL = """
SELECT Id, MessageDate, FromName, Subject, TextBody
FROM EmailMessage WHERE ParentId IN (SELECT Id FROM Case WHERE AccountId = '{account_id}')
ORDER BY MessageDate DESC LIMIT 50
"""


def connect(settings: Settings) -> Salesforce:
    missing = settings.require_salesforce_env()
    if missing:
        raise RuntimeError(f"Missing Salesforce settings: {', '.join(missing)}")
    if settings.sf_username and settings.sf_password:
        return Salesforce(username=settings.sf_username, password=settings.sf_password,
                          security_token=settings.sf_security_token,
                          consumer_key=settings.sf_client_id, consumer_secret=settings.sf_client_secret,
                          domain=settings.sf_domain.replace("https://", "").split(".")[0] or None)
    return Salesforce(domain=settings.sf_domain.replace("https://", "").split(".")[0],
                      consumer_key=settings.sf_client_id, consumer_secret=settings.sf_client_secret)


def _account_id(sf: Salesforce, account_number: str) -> str | None:
    safe = account_number.replace("'", "\\'")
    res = sf.query(f"SELECT Id FROM Account WHERE {ACCOUNT_NUMBER_FIELD} = '{safe}' LIMIT 1")
    records = res.get("records", [])
    return records[0]["Id"] if records else None


def fetch_notes_for_account(sf: Salesforce, account_number: str) -> list[SalesforceNote]:
    """All client<->advisor notes/comments for one brokerage account number. Returns an empty list
    (not an error) when the account isn't found in Salesforce, since not every brokerage account
    is necessarily linked to a Salesforce Account yet."""
    account_id = _account_id(sf, account_number)
    if not account_id:
        return []
    notes: list[SalesforceNote] = []
    for rec in sf.query_all(NOTES_SOQL.format(account_id=account_id)).get("records", []):
        notes.append(SalesforceNote(source="Case", record_id=rec["Id"], author=(rec.get("CreatedBy") or {}).get("Name", ""),
                                    created_at=rec.get("CreatedDate", ""), subject=rec.get("Subject") or "",
                                    body=rec.get("Description") or ""))
    for rec in sf.query_all(COMMENTS_SOQL.format(account_id=account_id)).get("records", []):
        notes.append(SalesforceNote(source="CaseComment", record_id=rec["Id"],
                                    author=(rec.get("CreatedBy") or {}).get("Name", ""),
                                    created_at=rec.get("CreatedDate", ""), body=rec.get("CommentBody") or ""))
    for rec in sf.query_all(TASKS_SOQL.format(account_id=account_id)).get("records", []):
        notes.append(SalesforceNote(source="Task", record_id=rec["Id"], author=(rec.get("Owner") or {}).get("Name", ""),
                                    created_at=rec.get("CreatedDate", ""), subject=rec.get("Subject") or "",
                                    body=rec.get("Description") or ""))
    for rec in sf.query_all(EMAILS_SOQL.format(account_id=account_id)).get("records", []):
        notes.append(SalesforceNote(source="EmailMessage", record_id=rec["Id"], author=rec.get("FromName") or "",
                                    created_at=rec.get("MessageDate", ""), subject=rec.get("Subject") or "",
                                    body=rec.get("TextBody") or ""))
    notes.sort(key=lambda n: n.created_at, reverse=True)
    return notes
