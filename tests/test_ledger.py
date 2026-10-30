from orchestrator.ledger import ComplianceLedger, make_case_id


def test_make_case_id_strips_punctuation():
    assert make_case_id("20261030_120000", "A-1 B") == "RECON-20261030_120000-A1B"


def test_create_get_update(tmp_path):
    ledger = ComplianceLedger(str(tmp_path / "ledger.sqlite3"))
    case = ledger.create_case("run1", "A-1", ["out/x.xlsx", "out/x.pdf"], discrepancy_count=3, suspicious_count=1)
    assert case.status == "PENDING"
    assert case.report_paths == ["out/x.xlsx", "out/x.pdf"]

    fetched = ledger.get_case(case.case_id)
    assert fetched is not None and fetched.account == "A-1"

    updated = ledger.update_decision(case.case_id, "APPROVED", "officer@firm.com", "Looks fine.")
    assert updated.status == "APPROVED"
    assert updated.reviewer_email == "officer@firm.com"
    assert updated.reviewed_at

    assert ledger.get_case("does-not-exist") is None


def test_list_cases_filters(tmp_path):
    ledger = ComplianceLedger(str(tmp_path / "ledger.sqlite3"))
    c1 = ledger.create_case("run1", "A-1", [], 1, 1)
    ledger.create_case("run1", "A-2", [], 1, 1)
    ledger.update_decision(c1.case_id, "APPROVED", "x", "")

    assert len(ledger.list_cases(run_id="run1")) == 2
    assert [c.case_id for c in ledger.list_cases(status="PENDING")] == [
        c.case_id for c in ledger.list_cases(run_id="run1") if c.status == "PENDING"
    ]
    assert len(ledger.list_cases(status="APPROVED")) == 1


def test_duplicate_create_case_is_idempotent_replace(tmp_path):
    ledger = ComplianceLedger(str(tmp_path / "ledger.sqlite3"))
    ledger.create_case("run1", "A-1", [], 1, 1)
    ledger.update_decision(make_case_id("run1", "A-1"), "APPROVED", "x", "")
    # re-creating the same run/account case resets it to PENDING (e.g. a re-run)
    ledger.create_case("run1", "A-1", [], 2, 1)
    assert ledger.get_case(make_case_id("run1", "A-1")).status == "PENDING"
