import os
from datetime import datetime

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

from .models import Discrepancy

COLS = ["kind", "symbol", "trade_date", "side", "client_value", "advisor_value", "detail", "client_file", "advisor_file"]


def _sheet(ws, rows: list[Discrepancy]):
    ws.append([c.replace("_", " ").title() for c in COLS])
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="1F4E78")
    for r in rows:
        ws.append([getattr(r, c) for c in COLS])
    for i, c in enumerate(COLS, 1):
        w = max([len(str(x.value or "")) for x in ws[get_column_letter(i)]] + [10])
        ws.column_dimensions[get_column_letter(i)].width = min(w + 2, 60)
    ws.freeze_panes = "A2"


def build_excel(discrepancies: list[Discrepancy], out_dir: str, n_client: int, n_advisor: int) -> str:
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"trade_discrepancies_{datetime.now():%Y%m%d_%H%M%S}.xlsx")
    wb = Workbook()
    summ = wb.active
    summ.title = "Summary"
    summ.append(["Client trades", n_client])
    summ.append(["Advisor trades", n_advisor])
    summ.append(["Total discrepancies", len(discrepancies)])
    kinds = sorted({d.kind for d in discrepancies})
    for k in kinds:
        summ.append([k, sum(d.kind == k for d in discrepancies)])
    summ.column_dimensions["A"].width = 28
    _sheet(wb.create_sheet("Discrepancies"), [d for d in discrepancies if not d.kind.startswith("MISSING")])
    _sheet(wb.create_sheet("Unmatched"), [d for d in discrepancies if d.kind.startswith("MISSING")])
    wb.save(path)
    return path
