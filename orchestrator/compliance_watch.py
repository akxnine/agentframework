"""Closes the compliance approval loop: polls the compliance mailbox for replies to a
"[Case: <id>] ..." report email, classifies the decision, and updates the ledger and the
master tracker workbook so the account is recorded as reviewed.

Run once (`--once`) or as a long-lived poller (default). Deterministic keyword matching is
tried first (a reply that starts with "approve"/"reject"); anything else is classified by the
same compliance-analyst prompt used inside the orchestrator agent, called directly here as a
single forced-tool-use request since this runs as its own process, not inside the orchestrator's
agent loop.
"""
import asyncio
import glob
import logging
import os
import re
import time

from anthropic import AsyncAnthropic

from .agents import COMPLIANCE_ANALYST_PROMPT
from .config import Settings
from .excel_io import update_compliance_status
from .graph_client import list_unread_replies, mark_read, reply_to
from .ledger import ComplianceLedger

log = logging.getLogger("recon")

KEYWORD = re.compile(r"^\s*(approve[d]?|reject(?:ed)?)\b", re.I)
TOOL_NAME = "record_decision"
TOOL_SCHEMA = {
    "name": TOOL_NAME,
    "description": "Record the compliance decision extracted from an email reply.",
    "input_schema": {
        "type": "object",
        "properties": {
            "decision": {"type": "string", "enum": ["APPROVED", "REJECTED", "NEEDS_INFO"]},
            "notes": {"type": "string", "description": "the officer's comment, or a one-sentence reason if none"},
        },
        "required": ["decision", "notes"],
    },
}


def classify_deterministic(body: str) -> tuple[str, str] | None:
    m = KEYWORD.match(body or "")
    if not m:
        return None
    decision = "APPROVED" if m.group(1).lower().startswith("approv") else "REJECTED"
    notes = body[m.end():].strip(" .,-:\n\t") or "No additional comment."
    return decision, notes


async def classify_with_claude(client: AsyncAnthropic, model: str, body: str) -> tuple[str, str]:
    resp = await client.messages.create(
        model=model, max_tokens=512, temperature=0, system=COMPLIANCE_ANALYST_PROMPT,
        tools=[TOOL_SCHEMA], tool_choice={"type": "tool", "name": TOOL_NAME},
        messages=[{"role": "user", "content": f"Classify this compliance reply:\n\n{body}"}],
    )
    for block in resp.content:
        if getattr(block, "type", None) == "tool_use" and block.name == TOOL_NAME:
            data = block.input
            return data.get("decision", "NEEDS_INFO"), data.get("notes", "")
    return "NEEDS_INFO", "Could not classify the reply automatically."


def _update_trackers(settings: Settings, account: str, status: str, reviewer: str, reviewed_at: str,
                     notes: str) -> int:
    search_dirs = [settings.master_dir]
    if not settings.in_place:
        search_dirs.append(os.path.join(settings.output_folder, "master"))
    updated = 0
    for d in search_dirs:
        for path in glob.glob(os.path.join(d, "*.xlsx")):
            try:
                if update_compliance_status(path, account, status, reviewer, reviewed_at, notes):
                    updated += 1
            except Exception as e:  # noqa: BLE001 - keep polling other files
                log.warning("could not update %s: %s", path, e)
    return updated


async def process_once(settings: Settings, ledger: ComplianceLedger, anthropic_client: AsyncAnthropic | None) -> int:
    """One poll pass. Returns the number of replies processed."""
    replies = list_unread_replies(settings)
    processed = 0
    for reply in replies:
        case = ledger.get_case(reply["case_id"])
        if case is None:
            log.warning("reply references unknown case %s, skipping", reply["case_id"])
            mark_read(settings, reply["id"])
            continue
        if case.status != "PENDING":
            log.info("case %s already resolved (%s), skipping duplicate reply", case.case_id, case.status)
            mark_read(settings, reply["id"])
            continue

        decision = classify_deterministic(reply["body_text"])
        if decision is None and anthropic_client is not None:
            decision = await classify_with_claude(anthropic_client, settings.model, reply["body_text"])
        elif decision is None:
            decision = ("NEEDS_INFO", "Could not classify automatically and no Anthropic API key is configured.")
        status, notes = decision

        updated_case = ledger.update_decision(case.case_id, status, reply["from_address"], notes)
        reviewed_at = updated_case.reviewed_at if updated_case else ""
        n = _update_trackers(settings, case.account, status, reply["from_address"], reviewed_at, notes)
        log.info("case %s -> %s by %s (%d tracker row(s) updated)", case.case_id, status, reply["from_address"], n)

        ack = (f"Recorded: case {case.case_id} for account {case.account} marked {status}. "
              f"Notes: {notes}" if notes else f"Recorded: case {case.case_id} marked {status}.")
        try:
            reply_to(settings, reply["id"], ack)
        except Exception as e:  # noqa: BLE001 - the decision is already recorded; don't fail the pass over this
            log.warning("could not send acknowledgement for case %s: %s", case.case_id, e)
        mark_read(settings, reply["id"])
        processed += 1
    return processed


async def watch(settings: Settings, once: bool = False) -> None:
    missing = settings.require_email_env()
    if missing:
        raise RuntimeError(f"Missing email settings: {', '.join(missing)}")
    ledger = ComplianceLedger(settings.ledger_path)
    anthropic_client = AsyncAnthropic(api_key=settings.anthropic_api_key) if settings.anthropic_api_key else None
    while True:
        n = await process_once(settings, ledger, anthropic_client)
        if n:
            log.info("processed %d compliance reply(ies)", n)
        if once:
            return
        await asyncio.sleep(settings.compliance_poll_seconds)


def main() -> None:
    from .console import setup_logging
    setup_logging()
    import argparse
    p = argparse.ArgumentParser(description="Poll the compliance mailbox and close out approve/reject replies")
    p.add_argument("--once", action="store_true", help="Process pending replies once and exit")
    args = p.parse_args()
    asyncio.run(watch(Settings(), once=args.once))


if __name__ == "__main__":
    main()
