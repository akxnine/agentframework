import openpyxl
import pymupdf

from recon.compare import compare_trades
from recon.extraction import read_pdf
from recon.models import Trade
from recon.report import build_excel


def T(sym, side, qty, px, acct="A1", d="2026-01-02"):
    return Trade(symbol=sym, trade_date=d, side=side, quantity=qty, price=px, account=acct)


def kinds(c, a):
    return sorted(d.kind for d in compare_trades(c, a))


def test_buy_client_pays_more_flagged():
    assert kinds([T("X", "BUY", 1, 10.5)], [T("X", "BUY", 1, 10)]) == ["PRICE"]


def test_buy_client_pays_less_ok():
    assert kinds([T("X", "BUY", 1, 9.5)], [T("X", "BUY", 1, 10)]) == []


def test_sell_client_gets_less_flagged():
    assert kinds([T("X", "SELL", 1, 9.5)], [T("X", "SELL", 1, 10)]) == ["PRICE"]


def test_sell_client_gets_more_ok():
    assert kinds([T("X", "SELL", 1, 10.5)], [T("X", "SELL", 1, 10)]) == []


def test_qty_account_and_missing():
    c = [T("X", "BUY", 2, 10, "A1"), T("Y", "BUY", 1, 1)]
    a = [T("X", "BUY", 3, 10, "A2"), T("Z", "SELL", 1, 1)]
    assert kinds(c, a) == ["ACCOUNT", "MISSING_IN_ADVISOR", "MISSING_IN_CLIENT", "QUANTITY"]


def test_excel(tmp_path):
    d = compare_trades([T("X", "BUY", 1, 11)], [T("X", "BUY", 1, 10)])
    p = build_excel(d, str(tmp_path), 1, 1)
    wb = openpyxl.load_workbook(p)
    assert wb.sheetnames == ["Summary", "Discrepancies", "Unmatched"]
    assert wb["Discrepancies"].max_row == 2


def test_read_pdf_text_vs_scan(tmp_path):
    doc = pymupdf.open()
    doc.new_page().insert_text((50, 50), "Trade " * 40)
    doc.new_page()  # blank page -> treated as scan
    p = tmp_path / "a.pdf"
    doc.save(p)
    text, images = read_pdf(str(p))
    assert "Trade" in text and len(images) == 1
