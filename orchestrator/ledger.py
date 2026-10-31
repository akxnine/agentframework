"""Compliance ledger: durable record of every flagged account that was emailed to compliance,
and the decision that eventually closed it out. SQLite so the CLI run and the separate
compliance-watcher process (and the Streamlit dashboard) can all see the same state.
"""
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .models import ComplianceCase

SCHEMA = """
CREATE TABLE IF NOT EXISTS compliance_cases (
    case_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    account TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING',
    created_at TEXT NOT NULL,
    reviewed_at TEXT DEFAULT '',
    reviewer_email TEXT DEFAULT '',
    decision_notes TEXT DEFAULT '',
    report_paths TEXT DEFAULT '',
    discrepancy_count INTEGER DEFAULT 0,
    suspicious_count INTEGER DEFAULT 0
);
"""


def make_case_id(run_id: str, account: str) -> str:
    safe = "".join(ch for ch in account if ch.isalnum()).upper()
    return f"RECON-{run_id}-{safe}"


class ComplianceLedger:
    def __init__(self, path: str) -> None:
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.execute(SCHEMA)

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def create_case(self, run_id: str, account: str, report_paths: list[str], discrepancy_count: int,
                    suspicious_count: int) -> ComplianceCase:
        case = ComplianceCase(case_id=make_case_id(run_id, account), run_id=run_id, account=account,
                              status="PENDING", created_at=datetime.now(timezone.utc).isoformat(),
                              report_paths=report_paths, discrepancy_count=discrepancy_count,
                              suspicious_count=suspicious_count)
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO compliance_cases "
                "(case_id, run_id, account, status, created_at, report_paths, discrepancy_count, suspicious_count) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (case.case_id, case.run_id, case.account, case.status, case.created_at,
                 "|".join(report_paths), discrepancy_count, suspicious_count))
        return case

    def get_case(self, case_id: str) -> ComplianceCase | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM compliance_cases WHERE case_id = ?", (case_id,)).fetchone()
        return _row_to_case(row) if row else None

    def update_decision(self, case_id: str, status: str, reviewer_email: str, decision_notes: str) -> ComplianceCase | None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE compliance_cases SET status = ?, reviewer_email = ?, decision_notes = ?, reviewed_at = ? "
                "WHERE case_id = ?",
                (status, reviewer_email, decision_notes, datetime.now(timezone.utc).isoformat(), case_id))
        return self.get_case(case_id)

    def list_cases(self, status: str | None = None, run_id: str | None = None) -> list[ComplianceCase]:
        query, params = "SELECT * FROM compliance_cases", []
        clauses = []
        if status:
            clauses.append("status = ?"); params.append(status)
        if run_id:
            clauses.append("run_id = ?"); params.append(run_id)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at DESC"
        with self._conn() as conn:
            rows = conn.execute(query, params).fetchall()
        return [_row_to_case(r) for r in rows]


def _row_to_case(row: sqlite3.Row) -> ComplianceCase:
    return ComplianceCase(case_id=row["case_id"], run_id=row["run_id"], account=row["account"], status=row["status"],
                          created_at=row["created_at"], reviewed_at=row["reviewed_at"] or "",
                          reviewer_email=row["reviewer_email"] or "", decision_notes=row["decision_notes"] or "",
                          report_paths=[p for p in (row["report_paths"] or "").split("|") if p],
                          discrepancy_count=row["discrepancy_count"], suspicious_count=row["suspicious_count"])
