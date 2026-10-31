from orchestrator.salesforce_client import fetch_notes_for_account


class FakeSalesforce:
    def __init__(self, account_id: str | None, by_query: dict[str, list[dict]]):
        self._account_id = account_id
        self._by_query = by_query

    def query(self, soql: str) -> dict:
        if soql.strip().startswith("SELECT Id FROM Account"):
            return {"records": [{"Id": self._account_id}]} if self._account_id else {"records": []}
        return {"records": self._by_query.get(_kind(soql), [])}

    def query_all(self, soql: str) -> dict:
        return {"records": self._by_query.get(_kind(soql), [])}


def _kind(soql: str) -> str:
    # Order matters: CaseComment/EmailMessage must be checked before the plain "Case" substring match.
    for kind in ("CaseComment", "EmailMessage", "Task", "Case"):
        if f"FROM {kind} " in soql:
            return kind
    return ""


def test_fetch_notes_returns_empty_when_account_not_found():
    sf = FakeSalesforce(account_id=None, by_query={})
    assert fetch_notes_for_account(sf, "UNKNOWN-ACCT") == []


def test_fetch_notes_normalises_all_sources():
    by_query = {
        "Case": [{"Id": "c1", "CreatedDate": "2026-10-01T00:00:00Z", "CreatedBy": {"Name": "Advisor A"},
                  "Subject": "Review", "Description": "Client called about pricing."}],
        "CaseComment": [{"Id": "cc1", "CreatedDate": "2026-10-02T00:00:00Z", "CreatedBy": {"Name": "Advisor A"},
                         "CommentBody": "Followed up by phone."}],
        "Task": [{"Id": "t1", "CreatedDate": "2026-10-03T00:00:00Z", "Owner": {"Name": "Advisor A"},
                  "Subject": "Call", "Description": "Left voicemail."}],
        "EmailMessage": [{"Id": "e1", "MessageDate": "2026-10-04T00:00:00Z", "FromName": "client@example.com",
                          "Subject": "Re: trade", "TextBody": "Please confirm the price."}],
    }
    sf = FakeSalesforce(account_id="001xx", by_query=by_query)
    notes = fetch_notes_for_account(sf, "Z12-345678")
    assert {n.source for n in notes} == {"Case", "CaseComment", "Task", "EmailMessage"}
    assert notes[0].created_at >= notes[-1].created_at  # sorted newest first
    by_source = {n.source: n for n in notes}
    assert by_source["EmailMessage"].body == "Please confirm the price."
    assert by_source["Task"].author == "Advisor A"
